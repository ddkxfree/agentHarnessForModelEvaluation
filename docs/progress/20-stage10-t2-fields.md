# 20 · 阶段十 T2 小补修：逐字段检查任务条目，条件解释与整体一致

日期：2026-10-24。对应 [19](19-stage10-fix-review.md) 的 T2 残留。只用 MockModel／FakeProvider 测试，没有调用真实模型；真实消融仍待验证。

## 问题

`_extra_changes` 把 manifest 中 `tasks.*` 下的差异整体视为改动参数的派生结果。所以改了生成温度、评分器指纹或数据集版本，报告仍然支持机制消融归因，`scores_comparable` 也仍为真。另外，运行中断时总体解释为 `inconclusive`，单个条件的解释却可能还是 `mechanism_ablation`。

## 修复

- 任务条目按字段检查。只有以下差异视为改动参数的派生结果：
  - 改动参数本身：`run_config.params.<参数>`、`tasks.<任务>.params.<参数>`；
  - `solver.*`：solver 描述和指纹；
  - `solver_protocol.*`：例如协议中的截断说明；
  - `cache.namespace`：每个条件有自己的缓存命名空间；
  - `time_limit`、`token_limit`：由预算检查单独比较。

  其他任务字段的差异都算额外变化，例如 `generate_config.*`、`scorer.*`、`dataset.*`、`n_samples`、`sample_ids_sha256`、`sandbox_provider.*`、`cache.policy` 以及其他参数。命中任何一项，解释都是 `inconclusive`。
- 评分相关字段（`scorer`、`dataset`、`n_samples`、`sample_ids_sha256`）在条件之间不同时，`scores_comparable` 改为假，并增加一条整体原因"what is scored or how differs between conditions"。
- 每个条件的 `interpretation.reasons` 先列适用于所有条件的整体原因，再列本条件自己的原因。所以条件的解释不会比整体更乐观。
- 限制：机制消融只允许改变 solver 侧。改动参数如果本来就是用来改变评分规则或数据的（比如有意比较两种评分器），会被判为 `inconclusive`。这类对照不属于"同评分规则下的机制归因"，按设计如此。

## 测试（`tests/test_budget.py`）

- `test_changes_inside_the_task_entry_are_not_attributed`：分别修改 careful 条件 manifest 中的四个字段，期望结果如下。

  | 修改的字段 | 解释 | `scores_comparable` |
  |---|---|---|
  | `generate_config.temperature` | `inconclusive` | 仍为真 |
  | `scorer.fingerprint` | `inconclusive` | 假 |
  | `dataset.revision` | `inconclusive` | 假 |
  | `sample_ids_sha256` | `inconclusive` | 假 |

- `test_incomplete_runs_are_not_attributed`：增加断言，运行中断时条件自身的解释也是 `inconclusive`，并带上整体原因。
- `test_observation_ablation_of_the_agent_stays_attributable`：用真实的 `terminal` 任务、BasicAgent 和 FakeProvider 跑一次 `max_observation_chars` 4000 对 500 的消融。差异中包含 solver 描述和协议的截断说明，解释仍为 `mechanism_ablation`，分数可比。
- 用修复前的 `compare.py` 运行，前两类新增断言共 5 个失败，与 19 的复现一致。

## 验证

`uv run pytest -q`：265 passed（含 swebench extra，本机 Docker 实际运行）；`ruff check`、`ruff format --check` 通过。
