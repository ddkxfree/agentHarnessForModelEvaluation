# 28 · 阶段八补修 O1～O3：容器验证、冻结预期样本、整次尝试的截止时间

日期：2026-10-25。基准：master `258274d`（含 [27](27-onboarding-review.md) 的评审）。只修 O1～O3，不新增评测集、不调用真实模型；**状态：补修与 MockModel／Docker 回归完成，真实模型接入仍待验证**。

## O1 [P1] 候选代码默认在容器中验证

新增 `DockerExecutor`，成为规格的默认执行器（`executor: docker`）。每次检查（独立检查、候选自测）各起一个容器：

| 限制 | 设置 |
|---|---|
| 网络 | `network_mode=none` |
| 文件系统 | 根文件系统只读；只有临时验证目录（挂载为 `/work`，内含候选文件副本、数据副本、一次性 payload）可写；`/tmp` 为 64 MB tmpfs |
| 平台代码 | `harness` 包只读挂载到 `/opt/harness/harness`，仓库其余部分（样例的验收文件、预期样本、源码树）不挂载 |
| 权限 | 以宿主当前 uid:gid 运行，`cap_drop=ALL`、`no-new-privileges` |
| 资源 | 内存 1 GB（不允许 swap）、1 CPU、最多 256 个进程 |
| 清理 | 超时或取消时 kill，结束后无论成败都删除容器 |

镜像 `harness-onboard:<Dockerfile 指纹>` 由 `python:3.12-slim` 加上控制器当前版本的 pydantic、numpy、pytest 构建，首次使用时构建一次（本机约 10 秒）。报告的 `executor` 字段记录隔离级别、镜像 tag 与 ID、Dockerfile 和各项限制。Docker 不可用时抛出 `SandboxError`，记为 `env_error`，**不会退回宿主子进程**。

`SubprocessExecutor` 保留，但只在显式选择时使用（`executor: subprocess` 或 `--executor subprocess`），报告中标为 `isolation: none`，用于可信的脚本化候选：单元测试和 `examples/onboard/demo.py`。它现在在独立进程组中运行，超时或取消时整组 kill。

执行器接口改为 `run(argv, *, workdir, timeout)`、`cancel()`、`visible_path(workdir)`、`describe()`：命令中的路径都相对于 `workdir`，`python` 由执行器替换为实际解释器，这样同一条命令在宿主和容器中都能运行。诊断中的临时目录（宿主路径或 `/work`）都替换为 `<sandbox>`，回放仍然稳定。

`harness onboard` 和 `harness onboard-verify` 新增 `--executor`，`configs/onboard-world-capitals.yaml` 显式写明 `executor: docker`。

回归（需要 Docker，不可用时跳过）：

- adapter 在导入时写临时验证目录之外的宿主路径：验证失败（`import_error`），宿主上没有出现该文件；
- adapter 写容器内看到的 `harness` 包目录：`Read-only file system`，宿主包目录不变；
- 候选自测写宿主路径、连接外网：两项都失败，宿主文件不存在；
- 正确 adapter 在容器中 17 个用例全部通过，诊断中不出现 `/work`；
- 睡眠的 adapter：总时限 3 秒时记为 `validation_timeout`；另一个线程调用 `cancel()` 后容器被 kill，结束后没有遗留容器；
- 对照：同一个越界 adapter 用 `SubprocessExecutor` 会写到宿主并通过，说明子进程只适合可信代码。

## O2 [P1] 冻结预期样本与模型实际看到的题目

样例新增 `expected.jsonl`，由人根据说明逐条写出每个样本的 `sample_id`、`input`、`target` 和模型应看到的 `prompt`。它和验收文件一样受保护：Agent 看不到、不能写（写入路径会被拒绝），计入源包哈希，规格字段 `expected`。README 补充说明了 input 就是该问题原文、它就是给模型的完整题目、target 是 `capital`、`alt_names` 只是可接受答案。源包哈希更新为 `be9339c0742eaa0e`。

检查进程（`onboard_check_v2`）新增两步，位于协议检查之后、正反例之前：

- `fields`：样本 ID 集合必须与预期一致；每个样本的 input、target 非空，去掉首尾空白后与预期相同；
- `prompts`：每个预期样本用平台的 `run_eval` 实际跑一次，记录 Solver 发给模型的消息。最后一条 user 消息必须等于预期 prompt；预期 prompt 不含答案时，任何一条消息（system、few-shot 等）都不能出现答案。

协议检查中"两次加载一致"改为比较完整样本内容，不只比较 ID。失败类别为新的 `field_error`（加载内容变化仍是 `protocol_error`），诊断列出具体样本与差异，作为反馈交给 Agent 修复。

payload（验收用例、预期样本、校验码）不再走 stdin，而是写入验证目录中的文件，检查进程读取后**在导入候选代码前删除**。

回归（`test_frozen_expected_samples_catch_field_mistakes`，均不通过且诊断指向原因）：空 input、把 capital 当 input（评审的复现）、只给国家名、题目漏掉国家、target 用错列、两次加载 ID 相同但内容变化、提示词直接是答案、few-shot 中给出本题答案。另有测试确认候选导入时 payload 已不存在。

已知限制：候选代码与检查代码在同一个进程中运行，恶意代码理论上可以从进程内存读到预期样本或校验码。本轮针对的是错误映射，不是对抗性候选；要彻底防住需要把候选的加载和检查分到不同进程，留作后续。

## O3 [P2] `time_limit_s` 是整次尝试的截止时间

- 模型调用：整个 `retry_async`（每次等待、超时重试和退避）包在 `asyncio.timeout(剩余时间)` 中；截止时间到达时取消在途请求，记为 `time_limit`，该步的错误和耗时写入记录。
- 验证：在工作线程中运行，`validate(..., timeout=剩余时间)`，独立检查和候选自测都只拿到剩余时间（同时不超过 `validation_timeout_s`），时间到由执行器 kill 进程或容器；自测开始前已无时间则记为 `skipped`。
- 结果：验证返回后若已过截止时间，不论是否通过，尝试都以 `time_limit` 结束，不会记为 passed。
- 取消：外部取消尝试时调用执行器的 `cancel()`，kill 正在运行的检查进程或容器，并把尝试记为 `cancelled`，已写的候选文件、轨迹和 diff 保留。

回归使用真实异步等待，不再用假时钟：

- 慢模型（第二次调用等 5 秒）在 0.3 秒总时限下 2 秒内停止；
- 重试退避本应等 5～10 秒，在总时限内停止；
- adapter 导入时睡 60 秒：总时限 1.5 秒时尝试以 `time_limit` 结束，检查进程确认已被 kill；
- 取消正在验证的尝试：检查进程被 kill，记录为 `cancelled`，候选文件保留；
- 执行器忽略时限、通过的报告在截止时间之后才返回：该轮验证记录为通过，但尝试以 `time_limit` 失败。

## 验证

- `uv run pytest -q`：378 passed（含 Docker 用例；onboarding 测试 52 个）。
- 只装基础依赖的环境：353 passed、8 skipped（可选依赖用例跳过）。
- `ruff check`、`ruff format --check` 通过。
- `uv run python examples/onboard/demo.py /tmp/onboard-demo`：第一轮 `protocol_error`，第二轮通过。

## 待验证

真实模型接入仍按 [25](25-onboarding-agent.md) 的命令在本地或通过 API 运行，现在默认需要 Docker：

```bash
harness onboard -c configs/onboard-world-capitals.yaml -m ollama/qwen2.5:7b-instruct
harness onboard -c configs/onboard-world-capitals.yaml -m deepseek/deepseek-chat
harness onboard-verify runs/onboard/<attempt>
```

首次运行会构建 `harness-onboard` 镜像，需要能访问 PyPI。
