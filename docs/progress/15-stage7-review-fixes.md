# 15 · 阶段七补修：条件间缓存隔离、回放与推理指标分开、部分分数的波动统计

日期：2026-10-24。对应 [14](14-stage7-review.md) 的 S1～S3，以及文档边界 1～3（第 4 条路线图表述已由评审方更新）。只用 MockModel 测试，没有调用真实模型。

## S1 不同条件共享请求缓存

- 原因：compare 的缓存命名空间只区分 epoch，不区分条件。像 `max_steps` 这类不进入提示词的参数，两个条件的请求完全相同，后一个条件会回放前一个条件刚写入的响应。
- 修复：`compare.condition_namespace` 为每个条件生成 `<对照命名空间>/<条件名>`，再加上 epoch，即"实验／条件／epoch"三层。同一条件、同一 epoch 的恢复仍然回放；不同条件之间默认不复用。没有提供"条件间共享响应"的模式。
- 测试 `test_statistics.py::test_conditions_do_not_share_cached_responses`：消融参数 `budget` 不进入提示词，两个条件请求相同；2 个条件 × 2 次重复 × 4 题调用模型 16 次（修复前为 8 次），`more` 条件的缓存调用为 0。

## S2 回放的耗时与 token 和首次推理混在一起

- runner 在每个样本上记录 `calls`：`fresh`（本次真正调用模型的次数）、`cached`（从缓存回放的次数）、`fresh_usage`（本次实际消耗的 token）。summary 新增 `calls` 和 `fresh_tokens`；原来的 `tokens` 是全部响应的逻辑 token，含回放部分，含义不变。
- compare 的每次运行新增 `cache_state`：`fresh`（全部现调）、`cached`（全部回放）、`mixed`、`unrecorded`（旧记录没有该字段）；还有 `fresh_calls`、`cached_calls`、`fresh_input_tokens`／`fresh_output_tokens`，以及 `resumed`（是否由 `--resume` 补跑）。
- 报告把原来的 `comparable` 拆成两项：
  - `scores_comparable`：运行齐全、样本相同、配置无差异、实际环境无漂移，分数可以比较（回放的响应与原响应相同，不影响分数）；
  - `performance_comparable`：在此基础上所有运行都是 `fresh`，墙钟时间和本次 token 才代表推理开销。
  报告还给出每次运行的 `cache_states`，命令行会列出含回放的运行。
- 恢复是请求级回放（见 13 第 2 节补充）：重跑的运行会重新创建沙箱、执行工具、评分，只有模型请求来自缓存，不是跳过已完成样本的检查点恢复。
- 测试：`test_resume_reruns_only_missing_runs_and_replays_their_cache` 增加断言：恢复后分数可比、性能不可比；补跑的 `plain#2`、`careful#2` 为 `cached`，`plain#2` 标记为 `resumed`，其本次 token 为 0、逻辑 token 为 40。`test_summary_separates_fresh_and_replayed_calls`：同配置重跑时 `calls` 由 `{fresh: 6, cached: 0}` 变为 `{fresh: 0, cached: 6}`。`test_clean_comparison_is_comparable`：全部现调时两项均为真。

## S3 部分分数被误判为跨 epoch 不一致

- `inconsistent_samples` 拆分为两个含义明确的指标：
  - `score_varies_samples`：各 epoch 分数不完全相同的样本数，适用于部分分数；
  - `outcome_varies_samples`：在某些 epoch 判为正确、另一些不是的样本数（按 outcome 判断）。
- 测试 `test_epoch_variation_handles_partial_scores`（5 例）：恒定 0.5、变化的部分分数、恒定 1、恒定 0、成功失败交替。

## 文档边界

13 中已补充：条件间缓存隔离与"新对照重新采样"的前提；请求级回放与指标口径；`ci95_over_samples` 的含义（按题目重抽样，不代表服务端随机性或未来运行的区间）；30 题只是展示阈值；中断续跑的验收命令固定独立命名空间，并说明依赖缓存开启。

## 对已有结果的影响

- compare 报告字段：`comparable` 改为 `scores_comparable` 和 `performance_comparable`，新增 `cache_states`。旧的对照目录用 `compare-report` 重建时，运行没有调用记录，状态为 `unrecorded`，因此 `performance_comparable` 为假。
- 对照的缓存键多了条件名，已有对照用 `--resume` 恢复时不会命中修复前写入的缓存。
- summary 的 `epochs.inconsistent_samples` 改名为两个字段。

## 验证

`uv run pytest -q`：238 passed（本机 Docker 实际运行）；`ruff check`、`ruff format --check` 通过。
