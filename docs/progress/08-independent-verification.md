# 08 · 独立复核（第五阶段加固，第三轮）

日期：2026-10-24。在 07（失败可追踪）的基础上开发。

## 目标

05 复评列出的第三轮验收项：

1. 完整的 patch 与日志，附哈希；
2. 实际镜像 ID／digest、平台架构、资源约束；
3. 服务端返回的模型标识（如果服务端提供）；
4. 明确的缓存策略：temperature > 0 时默认不复用；
5. 在新容器中验证已保存的补丁，不重新调用模型；SWE-bench 可以不调模型重评。

## 修复前的状态（源码复核，见交接文档第 5 节）

- SWE-bench 补丁只保存在 `score.details.model_patch`，截断到 20000 字符；评测日志只留最后 3000 字符。
- SWE-bench 在 Agent 用过的同一个容器里判分（`same_container: true`）。
- `DockerProvider.describe()` 只返回 `{"name": "docker"}`，没有镜像 ID；`python:3.12-slim` 的镜像曾经漂移过，事后无法查清当时用的是哪个镜像。
- 不记录服务端实际返回的模型名。
- temperature 为 0.8 时缓存 key 与 0 不同，但同为 0.8 的两次运行会完全复用第一次的输出，重复采样形同虚设。
- `harness rescore` 跳过所有需要沙箱的评分器。

## 这一轮做了什么

### 1. 完整产物与哈希

SWE-bench 评分器把完整内容写成 artifact，路径为 `<run>/swebench/artifacts/<样本目录>/`。artifact 机制见 07。

| artifact | 内容 |
|---|---|
| `submission` | 从 Agent 容器收集的完整补丁（不截断） |
| `apply.log` | 应用补丁的完整过程（`git apply` → `--reject` → `patch --fuzz`） |
| `eval.log` | 官方评测脚本的完整输出 |

`SampleResult.artifacts` 中记录每个文件的 SHA-256 和字节数。`score.details` 不再保存截断的补丁，改为 `model_patch_sha256`／`model_patch_bytes`、`eval_log_sha256`，并保留简短的尾部方便 `harness show` 查看。

### 2. 环境身份

`DockerSandbox.describe()` 写入 `SampleResult.sandbox`（新容器评分时还有 `grading_sandbox`），新增以下字段：

- `image_id`：实际使用的镜像 ID（`sha256:...`）；
- `image_digests`：镜像的 registry digest，例如 `python@sha256:...`。本地构建的终端任务镜像没有 digest，标签本身就是构建目录的内容指纹；
- `image_platform`：例如 `linux/amd64`；
- `limits`：Docker **实际生效**的内存、CPU 和网络模式，不是我们请求的值。

`DockerProvider.describe()` 写入 manifest，新增 Docker daemon 的版本、API 版本、操作系统、平台架构、内核版本、CPU 数和内存。换一台机器或升级 Docker 后，`harness diff` 能直接看出来。

### 3. 服务端模型标识

- `ModelOutput` 新增 `served_model` 和 `system_fingerprint`。OpenAI 兼容接口从响应的 `model`、`system_fingerprint` 字段读取。
- 每个样本记录 `served_models`，取所有模型调用的去重结果，格式为 `模型名@fingerprint`。summary 按样本数统计 `served_models`。
- 服务端在运行中途换了模型版本时，summary 里会出现多个标识。
- 服务端不返回这些字段时，记录为空。只能记录服务端愿意给出的信息。

### 4. 缓存策略

`runner.use_cache(policy, config)`，配置项 `cache_policy`，命令行参数为 `--cache-policy`：

| 策略 | 行为 |
|---|---|
| `auto`（默认） | 只在 temperature = 0 时读写缓存；采样运行每次都重新调用模型 |
| `always` | 总是读写缓存，例如采样运行中断后需要续跑 |
| `never` | 不使用缓存 |

manifest 每个任务新增 `cache: {enabled, policy, used}`，记录实际是否使用了缓存。多轮 epochs 和缓存命名空间留到阶段 7。

### 5. 新容器评分与 `harness verify`

**两阶段评分器**（`core/scorer.py::SubmissionScorer`，新增协议，对应官方 SWE-bench harness 的流程）：

- `collect(sample, text, sandbox)`：从 Agent 容器提取提交物（补丁）；
- `grade(sample, submission, sandbox)`：在全新的沙箱里判分。

runner 处理 `fresh_sandbox = True` 的评分器时，按以下顺序执行：

1. 调用 `collect`，把提交物保存为 `submission` artifact；
2. 关闭 Agent 容器；
3. 用同一个 spec 新建评分容器（标签 `harness.phase=grade`）；
4. 调用 `grade`，然后关闭评分容器。

新建容器失败记为 `env_error`。Agent 在 `/testbed` 之外做的改动（如安装的包）不再影响判分。

`SweBenchScorer` 改为这种两阶段评分器，指纹中 `same_container: true` 换成 `grading: fresh_container`，因此新旧运行的 scorer 指纹不同。

**`harness verify <run_dir>`**（`analysis/verify.py`，新增）：

- 对使用两阶段评分器的任务，逐个样本执行：
  1. 读取保存的 `submission`；
  2. 校验 SHA-256，不一致记为 `submission_hash_mismatch`，不评分；
  3. 用任务当前的 spec 新建容器，调用 `grade`，与记录的分数比较。
- 结果分为 `agree`、`disagree`、`newly_graded`（原来没有分数）、`scorer_error`、`env_error`、`no_submission`。逐样本写入 `<task>/verify.jsonl`；汇总写入 `verify.json`，其中包括评分容器的身份和 Docker daemon 信息。
- 默认只报告，不修改结果。加 `--update` 时用新分数替换旧分数，规则与 `rescore` 相同：
  - 只更新到达过评分阶段的样本；
  - 旧状态写入样本的 `rescore_history`，manifest 的 `rescore_history` 记一条 `kind: verify`；
  - 环境故障不修改样本。
- 评分器指纹与运行时不同时，命令会提示。
- `harness rescore` 对这类任务给出提示：改用 `harness verify`。
- 终端任务的评分依赖 Agent 留下的整个容器状态，与 Terminal-Bench 官方流程一致，没有可以单独保存的提交物，因此 `verify` 跳过并说明原因。

## 验证

`uv run pytest -q`：168 passed（Docker 集成测试 9 个，本机 Docker 29.1.3 实际运行，包括 SWE-bench `pytest-dev__pytest-5227` 的两个用例）。`ruff check`、`ruff format --check` 通过。

| 测试 | 验收项 |
|---|---|
| `test_verification.py::test_cache_policy`（4 例）、`test_unknown_cache_policy_is_rejected` | 4 |
| `test_verification.py::test_sampled_outputs_are_not_reused_by_default` | 4：T=0 时第二次运行 0 次调用；T=0.8 时第二次运行仍为 6 次调用；manifest 记录 `used` |
| `test_verification.py::test_served_model_identity_is_recorded`、`test_openai_compatible_model_reads_the_served_model` | 3：中途换版本会出现两个标识；OpenAI 响应字段被读取 |
| `test_verification.py::test_submission_is_saved_and_graded_in_a_fresh_sandbox` | 1、5：提交物文件与哈希；评分沙箱是新的（没有 Agent 写的文件）；两个沙箱都关闭 |
| `test_verification.py::test_environment_failure_while_grading_is_env_error` | 5 |
| `test_verification.py::test_verify_regrades_saved_submissions_without_the_model` | 5：重新判分结果一致，生成 `verify.jsonl`／`verify.json`，rescore 给出提示 |
| `test_verification.py::test_verify_detects_tampering_and_disagreement` | 5：篡改提交物、评分不一致都能发现 |
| `test_verification.py::test_verify_update_replaces_grades_and_keeps_history`、`test_verify_reports_environment_failures_without_touching_results` | 5：`--update` 与历史；环境故障不修改结果 |
| `test_docker_sandbox.py::test_exec_files_timeout_and_isolation`（扩充） | 2：真实容器的镜像 ID、`python@sha256:` digest、`linux/` 平台、生效的 1 GiB／1 CPU／无网络；daemon 信息 |
| `test_docker_sandbox.py::test_swebench_gold_patch_resolves_and_no_change_does_not`（扩充） | 5：评分容器与 Agent 容器不同、镜像 ID 相同 |
| `test_docker_sandbox.py::test_swebench_run_can_be_reverified_without_the_model` | 1、5：真实 SWE-bench 运行生成 3 个 artifact；`verify` 在第三个容器中复现 `resolved`；改动补丁的一个字符后报告 `submission_hash_mismatch` |

## 对已有结果的影响

- SWE-bench 的 scorer 指纹变化，`score.details` 字段变化（不再有 `model_patch`）。旧运行没有 `submission` artifact，`verify` 报告 `no_submission`。
- 采样运行（temperature > 0）不再默认复用缓存。之前依赖这一行为续跑采样运行的，需要加 `--cache-policy always`。
- manifest 中 `sandbox_provider` 增加了 daemon 信息，每个任务增加了 `cache`。`harness diff` 会把这些显示为配置差异。

## 仍然存在的偏差（如实记录）

- SWE-bench 评分容器开启网络（评测脚本可能需要 `pip install`）。已核对 swebench 5.0.2 的 `harness/run_evaluation.py`：官方创建容器时同样不关闭网络，但额外加了 `cap_add=["SYS_ADMIN"]`，也不限制内存和 CPU。我们不加该权限，并限制为 4 GiB／2 CPU。目前所测实例不受影响；需要浏览器沙箱的实例（如 openlayers 的 karma 测试）可能因此结果不同。
- 只记录镜像 ID 和 digest，没有在运行前强制拉取或校验 digest。数据集给出的镜像名带 `:latest` 标签，同名镜像更新后，`verify` 会使用新镜像。比较 `verify.jsonl` 中的 `grading_sandbox.image_id` 与原记录可以发现这种变化。
- 终端任务无法独立重评（见上文），这与 Terminal-Bench 的官方流程一致。
