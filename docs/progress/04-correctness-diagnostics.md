# 04 · 正确性与故障诊断（第五阶段加固，第一轮）

## 背景

交接文档（`docs/PROJECT_HANDOFF.md`）交给 Codex 评审后，确定第五阶段先"把结果做可信"，再扩任务数量，分五轮进行：

1. **修正确性与诊断**（本轮）
2. 失败可追踪：中途异常保留轨迹、完成样本及时落盘、清理失败要记录、单题总时长限制
3. 独立复核：完整产物、环境身份、新容器评分、SWE-bench 不调模型重评
4. 接入真实 Terminal-Bench
5. 有意义的 Solver 对照实验

本轮开始前，先在 `master`（`cc5ce8c`）上逐条复现了评审提到的问题，都确认存在后才动手修。

## 修复前复现到的问题

| 问题 | 复现结果 |
|---|---|
| 评分器出 bug 被算成模型失败 | scorer 抛 `KeyError`，结果为 `api_error`，`error_kind=model` |
| NaN 分数 | `Score(value=nan)` 被归为 `wrong`，并且整个任务的 accuracy 变成 `nan` |
| NaN / Infinity 被当成数字 | `normalize_number("nan") == "nan"`，`normalize_number("inf") == "inf"` |
| 大整数精度丢失 | 用 float 比较，`10000000000000000000000000` 被规整成 `10000000000000000905969664`，两个不同的大数可能被判相等 |
| 非法提交被当成成功提交 | `submit` 参数不是合法 JSON 时，模型收到 "Error"，但循环照样以 `submit` 结束，没有机会改正 |
| 非字符串答案 | `{"answer": {"a": 1}}` 被转成 Python 的 `"{'a': 1}"` 当作答案 |
| 提交后的命令仍会执行 | 同一轮里 `submit` 之后的 `bash` 调用照样执行，提交后还在改环境 |
| CI 从未在 master 上运行 | `ci.yml` 写的是 `branches: [main]`，默认分支是 `master` |

## 这一轮做了什么

### 1. 失败分四类

`Outcome` 新增 `solver_error` 和 `scorer_error`，`ErrorKind` 对应新增 `solver` 和 `scorer`。现在每个失败都记在真正出错的一方：

| outcome | 谁的问题 | 判定方式 |
|---|---|---|
| `api_error` | 模型调用 | 平台的 `generate` 重试后仍失败，抛出 `ModelCallError` |
| `solver_error` | Agent / Solver 代码 | Solver 抛出的其他异常 |
| `scorer_error` | 评分器 | 评分器抛异常、返回的不是 `Score`、分数不是 0～1 之间的有限数 |
| `env_error` | 环境 | 任何阶段抛出 `SandboxError`，或者沙箱创建失败 |

实现上（`core/runner.py`），runner 记录当前执行到哪个阶段（建沙箱、求解、评分）来决定归属。`ModelCallError` 和 `SandboxError` 无论在哪个阶段出现，都分别归为模型和环境。mini-swe-agent 在工作线程里调用模型时抛出的 `ModelCallError` 会原样传回，同样记为 `api_error`。

### 2. 分数必须有效

- `Score.value` 加了约束：必须是 0～1 之间的有限数，NaN、Infinity、负数、大于 1 都会在构造时报错。
- runner 再检查一遍，防止有人用 `model_construct` 绕过校验。
- 无效分数记为 `scorer_error`，不会再让 accuracy 变成 NaN。

### 3. 数字精确比较

- `normalize_number` 改用 `Decimal`：大整数不再丢精度；`NaN` 和 `Infinity` 不算数字；`0.50` 规整为 `0.5`，`-0` 规整为 `0`。
- 标准答案本身不是数字时（数据问题），`NumberMatchScorer` 抛错，记为 `scorer_error`，不再算模型答错。`ChoiceScorer` 同理：标准答案不在选项字母里就报错。
- `NumberMatchScorer.describe()` 新增 `"normalization": "decimal_exact"`，所以 scorer 指纹变了。旧运行执行 `harness rescore` 时会在 `rescore_history` 里留下记录。

### 4. 基线 Agent 的提交校验

- `submit` 参数无法解析、不是 JSON 对象、`answer` 不是字符串或数字时，**不算提交**。模型会收到错误信息和 "Nothing was submitted; call submit again."，可以在下一步重试。
- 数字答案（如 `42`）转成字符串 `"42"` 接受；布尔值、对象、数组拒绝。
- 同一轮里 `submit` 之后的调用**不执行**，返回 "Not executed: submit was already called in this turn."。每个调用仍然有对应的 tool 消息，符合 OpenAI 的消息格式要求。
- `BasicAgent.describe()` 新增 `"loop_version": 2`，行为变化会体现在 solver 指纹里。

mini-swe-agent 不需要改：它的提交本来就要求退出码为 0，提交后剩下的命令也不会执行。

### 5. CI

`ci.yml` 的 push 触发分支改为 `master`。格式检查（`ruff format --check`）原本就在 CI 中。

## 验证

- 测试从 83 个增加到 117 个，全部通过（包括 6 个 Docker 集成测试，在 Docker 29.1.3 上运行）；`ruff check` 和 `ruff format --check` 通过。
- 新增测试对应上面每一个问题：

| 测试 | 覆盖 |
|---|---|
| `test_runner.py::test_scorer_failures_are_not_blamed_on_the_model`（4 种情况） | 评分器抛异常、NaN 分数、返回非 Score、构造时校验失败 |
| `test_runner.py::test_sandbox_error_in_scorer_is_env_error` | 评分时环境故障 |
| `test_runner.py::test_solver_bug_is_solver_error_and_model_failure_is_api_error` | Solver bug 与模型失败分开 |
| `test_runner.py::test_summary_stays_finite_and_counts_every_failure_kind` | 汇总不出现 NaN，四类都有计数 |
| `test_scorer.py::test_normalize_number`（新增 8 例）、`test_large_integers_are_compared_exactly` | NaN、Infinity、大整数 |
| `test_scorer.py::test_non_numeric_target_is_a_scorer_error_not_a_wrong_answer`、`test_choice_target_outside_letters_is_rejected` | 标准答案无效 |
| `test_scorer.py::test_score_value_must_be_finite_and_in_range` | Score 构造约束 |
| `test_agent.py::test_invalid_submit_is_rejected_and_the_agent_can_retry`（4 种情况）、`test_numeric_answer_is_accepted`、`test_calls_after_submit_in_the_same_turn_are_not_executed` | 提交校验 |
| `test_mini_swe_agent.py::test_model_call_failures_through_the_bridge_are_api_errors` | 跨线程的模型失败仍归为 `api_error` |

- CI 是否在 master 上触发，要等本轮合并后才能确认。【待合并后验证】

## 对已有结果的影响

- `samples.jsonl` 的格式向后兼容：旧文件里的 `error_kind` 只有 `model` 和 `env`，仍然能读。
- 旧运行中由评分器 bug 导致的 `api_error` 不会自动改类，需要重新运行。
- `NumberMatchScorer` 的判分结果只在 NaN、Infinity、超过 float 精度的大整数这几种边界情况下会变化。

## 下一轮

第二轮：失败可追踪。中途异常时保留已完成的轨迹；每完成一个样本就落盘；沙箱清理失败要记录；单题总时长上限。
