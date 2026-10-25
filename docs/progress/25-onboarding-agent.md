# 25 · 阶段八第一版：评测集接入 Agent

日期：2026-10-25。按 [阶段八开发指引](../stage8-plan.md) 实现三批交付：规格与独立验证、Agent 循环与归档、端到端演示。只用脚本化的 MockModel 回复测试，验证子进程真实运行；**状态：工作流与 MockModel 验收完成、真实接入待验证**。自建样例只证明流程，不代表已经自动适配了新的公开 Benchmark。

本轮分支从 master 新建，不包含阶段六 J1／J2 补修（[24](24-stage6-review-fixes.md)，单独的 PR）。两个分支都改了进度索引和路线图状态段，后合入的一方需要解决冲突。

## 组成

| 模块 | 作用 |
|---|---|
| `onboard/spec.py` | 接入规格：任务名、源包路径与内容哈希、说明／规则／数据／验收文件名、允许的导入、模型、生成参数、预算 |
| `onboard/workspace.py` | 候选工作区：只允许写 `adapter.py`、`test_adapter.py`、`config.yaml`、`NOTES.md` |
| `onboard/check.py` | 在独立进程中执行的检查：静态导入、导入、任务协议、正反例评分、数据副本是否被改 |
| `onboard/validator.py` | 控制器一侧的独立验证：准备隔离目录、运行检查与候选自测、核对源包哈希、生成结构化报告 |
| `onboard/agent.py` | 接入 Agent 循环、反馈修复、预算、归档、diff 导出、重新验证 |
| `examples/onboard/world-capitals/` | 冻结样例包：说明、TSV 数据（非本平台格式）、官方评分规则、独立正反例 |
| `configs/onboard-world-capitals.yaml` | 样例的接入规格，固定源包哈希 |

`core/` 没有修改。

## 规格与写入边界

- 规格必须固定源包内容哈希 `source_sha256`（`fingerprint_dir`，覆盖说明、数据、规则和验收文件）。开始时哈希不符就停止，记为 `source_changed`，不调用模型。
- 受保护的输入：说明、数据、评分规则、验收文件。Agent 能读到说明、规则和数据预览（每个数据文件前 8 行），读不到验收文件。
- Agent 只能写四个候选文件。回复中只要有一个路径越界（绝对路径、`..`、受保护文件、仓库路径如 `src/harness/tasks/x.py`），整条回复都不写入，尝试立即停止，记为 `protected_violation`，并记录被拒绝的路径和原因。
- 候选代码改动了自己拿到的数据副本，也记为 `protected_violation`，并且优先于它引起的其他错误。
- 候选代码中不在允许列表里的导入记为 `dependency_error`（静态检查，导入前就发现）。

## 独立验证

由控制器运行，不由 Agent 运行：

1. 在临时目录中放入候选文件副本和数据副本。原件、评分规则和验收文件不放进去；验收用例通过 stdin 传给检查进程。
2. 用新的 Python 进程运行 `python -m harness.onboard.check`：
   - 环境变量只保留 PATH 等必要项，API key 不会传入；
   - 有时间上限，超时记为 `validation_timeout`；
   - 报告必须带上控制器随机生成的校验码，候选代码伪造或中途退出导致没有有效报告时，记为 `validation_crashed`。
3. 检查顺序：
   - 静态检查：语法、导入白名单；
   - 导入：必须恰好注册规格中的任务名；
   - 协议：返回 `Task`、名称正确、`dataset.revision` 等于数据目录的内容指纹、样本 ID 非空且唯一、两次加载一致、带任务名前缀、包含验收用例涉及的全部样本、scorer 不需要沙箱、`describe()` 带 fingerprint；
   - 正反例：每个用例用 MockModel 输出固定答案，在平台的 `run_eval` 中完整运行一次（写 manifest），正例必须为 `correct`，反例必须为 `wrong`，评分出错也算失败。控制器会自动为每个涉及的样本加一个空输出的反例，所以"所有输出都给满分"的 scorer 无法通过。
4. 验证前后核对源包哈希，验证期间源包被改也记为 `protected_violation`。
5. 候选自测（`test_adapter.py`，用 pytest 运行）的结果只作为信息记录，不决定是否通过。
6. 报告中的临时目录路径替换为 `<sandbox>`，相同的候选文件得到相同的诊断，可以回放。

执行器是可替换的接口（`Executor`）：默认 `SubprocessExecutor`；无法启动时抛出 `SandboxError`，记为 `env_error`。

## Agent 循环

1. 规划：模型读说明、规则、数据预览，回复字段映射（至少 id、input、target）和计划；无法解析时告诉它原因并重新要求，消耗模型步数。计划中提出问题时，尝试停止，记为 `needs_human`，问题保存在记录中。
2. 写文件：模型用 `<file path="...">...</file>` 回复完整文件（允许外包代码块）。没有文件块或缺少 `adapter.py` 时，该轮记为 `generation_failed` 并反馈。回复 `<question>` 同样停止等待人工。
3. 独立验证，见上节。
4. 失败时把类别、失败的检查详情和自测状态反馈给模型，进入下一轮。环境故障和源包变化不反馈给模型，直接停止，因为它们不是候选代码的问题。

预算：

- `max_rounds`：写入并验证的轮数；
- `max_model_steps`：所有模型调用次数；
- `time_limit_s`：整个尝试的时间；
- `token_limit`：服务端报告的 token 总数（缓存命中不计）；
- `validation_timeout_s`：单次验证的时间。

每次调用模型前检查前四项，用完时停止，记为 `budget_exhausted`，`stop_reason` 写明是哪一项，`stop_detail` 写出最后一轮的类别。已写的文件、全部轨迹和 diff 都保留。

模型调用复用平台的重试（`retry_async`）、超时和磁盘缓存（命名空间 `onboard/<task>`）。调用失败记为 `generation_failed`。

## 归档

每次尝试一个目录（默认 `runs/onboard/<时间>-<task>/`）：

- `attempt.json`：
  - 尝试指纹，由规格、实际源包哈希、模型身份、提示词版本、回复格式版本、验证器版本计算；
  - 预算、用量、规划结果、问题；
  - 每一步模型调用：阶段、请求哈希、原始回复、usage、服务端模型、是否缓存、耗时、尝试次数、错误；
  - 每一轮写入的文件及哈希、被拒绝的路径、完整验证报告、类别；
  - 状态、停止原因与详情、最终文件哈希、diff 文件名、复现命令。
- `conversation.json`：完整对话。
- `candidate/`：最终候选文件。
- `final.diff`：把候选文件放到仓库路径（`src/harness/tasks/<module>.py`、`tests/test_<module>.py`、`configs/<task>.yaml`、`docs/onboard/<task>.md`），并在 `src/harness/tasks/__init__.py` 中加入导入。数据需要按复现命令另外复制。diff 只供人工评审，不会自动应用、合并或覆盖正式实现。
- `verify-<时间>.json`：每次重新验证的报告。

失败类别：

| 类别 | 含义 |
|---|---|
| `generation_failed` | 模型调用失败，或回复中没有可用的文件 |
| `import_error` | 语法错误、导入时出错 |
| `dependency_error` | 使用了不允许的导入，或模块不存在 |
| `protocol_error` | 字段映射或任务协议错误 |
| `scoring_error` | 正反例评分不符合预期，或评分出错 |
| `validation_timeout` | 验证超时 |
| `validation_crashed` | 检查进程没有给出有效报告 |
| `protected_violation` | 写到候选范围外、改动受保护输入或数据副本 |
| `env_error` | 验证环境无法运行 |
| `source_changed` | 源包与固定的哈希不符 |
| `budget_exhausted` | 轮数、步数、时间或 token 用完 |
| `needs_human` | Agent 提出了需要人决定的问题 |

## 复核与复现

- 用同一个缓存重新运行同一规格，已保存的回复会被回放：不调用模型，得到相同的轮次结果和最终文件，尝试指纹相同。
- `harness onboard-verify <attempt>` 不调用模型，重新验证已保存的最终候选：
  - 源包哈希与尝试记录不同时，报告 `source_changed`，说明旧验证不能沿用；
  - 候选文件与记录的哈希不同时，报告 `candidate_changed`。
- 更换源数据或评分规则会改变尝试指纹。

## 怎么用

```bash
# 端到端演示：脚本化回复，第一轮读错列，收到诊断后修复（不需要模型）
uv run python examples/onboard/demo.py /tmp/onboard-demo
uv run harness onboard-verify /tmp/onboard-demo/<attempt>

# 真实接入（待验证）：本地模型或外部 API
uv run harness onboard -c configs/onboard-world-capitals.yaml -m ollama/qwen2.5:7b-instruct
uv run harness onboard -c configs/onboard-world-capitals.yaml -m deepseek/deepseek-chat
uv run harness onboard-verify runs/onboard/<attempt>
```

演示的实际输出：

```text
passed (validated) in /tmp/onboard-demo2/20261025-140635-world-capitals
  round 1: protocol_error
    KeyError: 'id'
  round 2: passed
```

通过的尝试只说明候选实现通过了冻结的正反例。合入前要人工审阅 `final.diff`，并按复现命令复制数据。

## 测试

`tests/test_onboard.py` 32 个用例，对应开发指引的最小回归：

1. 合法 adapter 一次通过：4 项检查、11 个冻结用例加 6 个空输出用例；归档内容完整；Agent 看不到验收用例。
2. 第一轮读错列（`KeyError`），反馈中含有类别和错误，第二轮修复后通过，并断言修复回复是在收到反馈之后生成的；回复中没有文件时记为生成失败，也能修复。
3. "所有输出都给满分"的 scorer 加上能通过的自测，独立验证仍然拒绝（11 个反例被判为正确）；自测失败只作记录。
4. 写入数据、评分规则、验收文件、说明、`..`、绝对路径、仓库路径都被拒绝，候选目录为空，源包不变；候选代码清空数据副本被拒绝；不允许的导入记为依赖错误；源包哈希不符时不调用模型。
5. 轮数、步数、token、时间预算用完时停止，文件、轨迹和 diff 保留。
6. 执行器无法启动记为 `env_error` 且不反馈给 Agent；验证超时单独分类；模型调用失败记为生成失败；Agent 提问时停止。
7. 缓存回放不调用模型且结果相同；修改评分规则后重新验证报告 `source_changed`，新尝试的指纹不同；候选文件被改后重新验证报告 `candidate_changed`。
8. 导出的 diff 在仓库副本中 `git apply` 后，复制数据，新进程中 `get_task("world-capitals")` 能加载 6 个样本并正确评分，仓库本身不受影响。

另有：文件块解析、配置固定的哈希与样例一致、命令行 `onboard` 与 `onboard-verify`。

- 完整环境（含 extra，本机 Docker 实际运行）：`uv run pytest -q` 339 passed。
- 只装基础依赖：314 passed、8 skipped。
- `ruff check`、`ruff format --check` 通过。

## 已知限制

- 第一版只支持一种数据格式、单个 split、不需要沙箱的普通 scorer。
- 检查进程与候选代码在同一进程中运行，能防止失误和简单的捷径（例如读取验收文件），但防不住专门攻击检查器的代码。更强的隔离（容器、只读挂载、禁用网络）需要换用 Docker 执行器。
- 反馈中会写出未通过的验收用例的输出和预期，Agent 能看到部分正反例。指引要求诊断能反馈给 Agent，这是有意的取舍；正反例原件仍不交给 Agent，Agent 也不能修改。
- 真实模型的接入效果未验证。需要在本地或外部 API 上运行上面的命令，并人工审阅最终 diff。
