# 06 · 复评问题 R1～R3 修复（第五阶段加固，第一轮补修）

日期：2026-10-24。基线：`ed6bf04`（05 复评记录）。

## 背景

05 复评结论是第一轮暂不通过，列了三个必须修复的问题。本轮只修这三项，每项先写能暴露错误行为的回归测试，确认它在旧代码上失败，再改实现。

## R1 · fix-mean 验证脚本放过 NaN

**原因**：`abs(got - expected) > 1e-9` 遇到 NaN 时结果为 False，NaN 实现被判 `PASS`。

**修复**（`tasks/terminal_data/fix-mean/tests/test.sh`）：比较之前先检查返回值。

- 必须是 `int` 或 `float`，`bool` 不算数字；
- 必须是有限数（`math.isfinite`），NaN 和正负无穷直接 `FAIL`；
- 以上通过后才做容差比较。

验证脚本是任务目录的一部分，任务指纹（`fingerprint_dir`）随之改变，旧运行与新运行用 `harness diff` 能看出来。

**测试**（`tests/test_terminal_verifiers.py`，新增，不需要 Docker）：把验证脚本中的 `cd /app` 换成临时目录，直接用 bash 执行原脚本。

| 实现 | 期望 |
|---|---|
| 正确的参考实现 | 通过 |
| 任务原始 bug（除以 `len - 1`） | 失败 |
| 返回 `nan`、`inf`、`-inf`、`"2"`、`True`、`None`、`[2]` | 全部失败，输出 `FAIL` |

在旧脚本上运行：`nan`、`"2"`、`None`、`[2]` 四例不符合期望。其中 NaN 被判通过；后三例虽然退出码非零，但原因是 Python 抛出未捕获的异常，没有输出 `FAIL`。

Docker 回归（Oracle 全过、不操作全失败）仍然通过。

## R2 · `decimal_exact` 会把不同的长小数舍入成相等

**原因**：`Decimal.normalize()` 按当前 decimal context 的精度（默认 28 位）舍入。`0.12345678901234567890123456781` 和 `…782` 都规整为 `0.1234567890123456789012345678`。

**修复**（`core/scorer.py::normalize_number`）：只用不受 context 精度影响的精确操作。

- `Decimal(str)` 构造、比较、`to_integral_value`、`int()`、`format(value, "f")` 都不按精度舍入；
- 整数照旧输出 `str(int(value))`；
- 非整数先输出完整的定点形式，再去掉末尾的 0。值不是整数，小数部分一定有非零位，去尾零不会删到小数点前。

`NumberMatchScorer.describe()` 的 `normalization` 从 `decimal_exact` 改为 `decimal_exact_v2`，scorer 指纹随之变化。

**测试**（`tests/test_scorer.py`）：

- `test_normalization_does_not_depend_on_decimal_context`：在 context 精度为 3、5、28、100 时分别检查以下几点：
  - 长小数原样保留；
  - 两个不同的长小数规整后不相等；
  - 尾零形式（`…781000`）与原值相等；
  - `-1.2300E+5` → `-123000`，`1E-40` 展开为 40 位小数；
  - `10000000000000000000000001.0` 按整数输出。
- `test_long_decimals_are_compared_exactly`：复评中的两个长小数得分为 0，加尾零的同值得分为 1。

旧实现在这两组测试上失败。

## R3 · 修好评分器后重评，旧的 `scorer_error` 不会消失

**原因**：`rescore` 只更新 `score`，没清除上次评分失败留下的 `error`／`error_kind`；而 `classify` 优先看 `error_kind`，结果出现"得分 1.0、outcome 却是 `scorer_error`"。重评时评分器若再抛异常，整个命令会中断。

**修复**（`analysis/rescore.py`）：

- 新函数 `rescore_sample` 处理单个样本。只重评**到达过评分阶段**的样本：有输出，且 `error_kind` 为空或为 `scorer`。
- 模型、Solver、环境失败的样本原样保留，重评不会把它们变成成功。
- 重评成功：清除旧的评分错误，重新分类。
- 重评失败：评分器抛异常，或返回值不是有效的 `Score`，该样本记为 `scorer_error`（`score` 置空），然后继续处理其他样本。
- 校验逻辑提成 `core/scorer.py::check_score`，runner 与 rescore 共用。它检查返回类型以及分数是否为 0～1 之间的有限数，可防止 `model_construct` 绕过校验。
- 评分状态有变化时，旧状态写进样本的新字段 `rescore_history`（时间、原 scorer 指纹、原 `score`／`error`／`error_kind`／`outcome`）。旧失败不会丢，只是不再是当前状态。
- 返回结果和 CLI 输出新增 `scorer_errors` 计数；`samples_changed` 改为统计评分状态有变化的样本数。

`SampleResult.rescore_history` 默认为空列表，旧的 `samples.jsonl` 仍可读取。

**测试**（`tests/test_run_artifacts.py`）：

| 测试 | 覆盖 |
|---|---|
| `test_rescore_with_fixed_scorer_clears_old_scorer_error` | 复评中的复现步骤：先用会抛 `KeyError` 的评分器运行，再换正确评分器重评。旧故障被清除、写入历史；accuracy 与 outcome 统计一致 |
| `test_rescore_failures_are_per_sample_scorer_errors` | 重评时单个样本失败：只有该样本记为 `scorer_error`，旧分数进历史，其他样本照常 |
| `test_rescore_rejects_invalid_scores` | 评分器返回 NaN 分数（绕过构造校验），全部记为 `scorer_error` |
| `test_rescore_keeps_model_and_solver_failures` | `solver_error`、`api_error` 样本即使有输出也不被重评 |

前三个在旧实现上失败。

## 验证

- `uv run pytest -q`：135 passed，其中 Docker 集成测试 6 个（Docker 29.1.3，本机实际运行，未跳过）。
- `ruff check`、`ruff format --check` 通过。
- 05 中待核验的 CI：PR #5 合并后，master 的 push 已触发 CI 并成功（GitHub Actions 运行 40077885432、40079062555）。

## 对已有结果的影响

- fix-mean：只有返回非有限值或非数字的实现会从通过变为失败；任务指纹变化。
- `NumberMatchScorer`：只有超过 28 位有效数字的小数会改变判分；指纹变化，重评时留下 `rescore_history`。
- 已有运行可以直接 `harness rescore`；之前因评分器 bug 产生的 `scorer_error` 会在评分器修好后恢复。

## 下一轮

第二轮：失败可追踪。中途异常保留已完成的步骤；单样本完成即落盘；原始工具输出单独保存；清理失败记录容器身份；单样本总超时与取消（包括 mini-swe-agent 工作线程）。
