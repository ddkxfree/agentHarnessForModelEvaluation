# 13 · 阶段 7 最小版：重复实验、恢复、配对统计与失败报告

日期：2026-10-24。在 12（第五阶段补修）的基础上开发。按 [11](11-stage5-review-and-next.md) 的要求，复用现有 `compare`，不另起一套重复实验引擎。全部用 MockModel 开发和测试；本轮没有调用真实模型，只用已有的运行记录做离线检查。

## 1. epochs 与缓存命名空间（run 和 compare 共用）

- `harness run --epochs N`（配置项 `epochs`）：每个样本跑 N 次。`SampleResult.epoch` 记录第几次，`samples.jsonl` 中包含全部 epoch，manifest 记录 `epochs: [1, …, N]`。旧记录没有该字段时视为 epoch 1。
- 缓存键加入命名空间：`cache_namespace(namespace, epoch)`。
  - 不同 epoch 互不复用：temperature 0 时每个 epoch 也会重新调用模型；
  - 同一 epoch 重跑（例如中断后续跑）复用缓存；
  - `--cache-namespace`（配置项 `cache_namespace`）把两次独立尝试分开；
  - 没有命名空间的 epoch 1 与旧缓存键完全相同，已有缓存不失效。
- 每个 epoch 的 artifact 目录不同（`<样本>-e2`），沙箱标签加 `harness.epoch`。
- `compare` 的第 k 次重复就是 epoch k（`run_eval(first_epoch=k)`）。因为不同重复的缓存键已经不同，取消了上一轮"重复时禁止使用缓存"的限制，默认 `cache_policy` 改为 `always`；新建的对照如果没有指定命名空间，就用对照目录名作为命名空间，因此不会回放以前那次对照的输出。

## 2. 恢复语义

- `harness run`：用同一配置重跑，同一 epoch、同一命名空间的已完成请求从缓存读取（缓存策略允许时）。
- `harness compare --resume <对照目录>`：读取目录中记录的配置和命名空间；保留已完成的运行，`interrupted` 的运行和缺失的运行在同一 epoch、同一命名空间下重跑。被替换的运行记在 `index.json` 的 `superseded`，每次恢复记在 `resumed_at`。
- 测试中模拟崩溃（最后一次运行没有登记、前一次标记为中断）后恢复：模型调用 0 次，结果齐全且 `comparable`；用同一配置新建对照则重新采样（16 次调用）。

## 3. 统计

- `summary.epochs`（多于一个 epoch 时）：每个 epoch 的准确率、均值、标准差、按样本重抽样的置信区间，以及在不同 epoch 结果不一致的样本数。
- `summary.ci95` 改为按样本重抽样（同一样本先对各 epoch 取平均）。单 epoch 时结果不变；多 epoch 时，同一题的重复不会被当成独立证据而让区间变窄（有测试：3 个相同 epoch 的区间与 1 个 epoch 相同）。
- `stats.paired_difference`：同一批样本上 B 减 A 的平均差、配对 bootstrap 置信区间、B 更好／更差／持平的样本数、精确符号检验 p 值（二值结果时即 McNemar 精确检验）。样本少于 30 时标记 `enough_samples_for_claims: false`，命令行提示"样本太少，不作结论"。
- `compare` 报告新增 `paired_vs_baseline`（每题先算各条件在各次重复中的解出率）；各条件新增 `std_over_repeats` 和 `ci95_over_samples`。
- `harness diff` 按（样本，epoch）配对，新增 `paired` 统计。

## 4. `harness diff` 的可能原因提示

`likely_causes` 给出白话提示，只说明该往哪里查，不证明某个样本为什么翻转：

- manifest 差异按类别归因：模型／端点、生成参数、solver、scorer、数据集版本、样本集合、任务参数、缓存策略、epochs、Docker 主机、代码或依赖；
- 输出完全相同但分数不同的样本：说明评分变了；
- 实际沙箱环境（镜像 ID、digest、平台、限制）不同的样本；
- 服务端报告的模型标识不同；
- 有翻转但以上都没有：提示采样或服务端非确定性，或 manifest 未记录的变化。

## 5. 失败报告

`harness report <运行目录>` 写出 `report.md`：每个任务的结果分类表；每类失败按固定种子抽取若干例（默认 3），列出错误、分数、目标、结束原因、最后两步的命令和退出码（没有工具调用的步骤显示模型说了什么）以及输出尾部。相同种子输出完全相同。

离线检查：对 10 中保存的 mini 运行生成报告，没有工具调用的步骤直接显示 `[Makes bash tool call with … as arguments]`，与 12 的归因一致。对该次对照重建报告：mini 相对 basic 为 −0.222，95% 区间 [−0.667, 0]，3 个配对样本，提示样本太少不作结论。

## 验证

`uv run pytest -q`：231 passed（本机 Docker 实际运行）；`ruff check`、`ruff format --check` 通过。新增 `tests/test_statistics.py`（12 例）：

| 测试 | 内容 |
|---|---|
| `test_epoch_one_keeps_existing_cache_keys` | 旧缓存键不变；不同 epoch／命名空间的键不同；artifact 目录不同 |
| `test_epochs_are_sampled_separately_and_resume_within_an_epoch` | 3 个 epoch 调用 18 次；重跑 0 次；第 4 个 epoch 或新命名空间重新调用 |
| `test_epoch_statistics_and_ci_resample_samples_not_epochs` | 每 epoch 准确率、标准差、不一致样本数；重复 epoch 不让区间变窄 |
| `test_sign_test_and_paired_difference` | 符号检验数值；配对只用共同样本 |
| `test_comparison_reports_paired_differences` | compare 的配对差 0.5、2 胜 2 平、p = 0.5 |
| `test_resume_reruns_only_missing_runs_and_replays_their_cache` | 恢复语义（见上文） |
| `test_diff_explains_likely_causes`、`test_diff_with_no_recorded_difference_points_at_nondeterminism`、`test_diff_matches_samples_by_epoch` | 可能原因提示；按 epoch 配对 |
| `test_failure_report_samples_each_failure_type`、`test_report_cli_writes_markdown` | 报告内容可复现；命令行 |
| `test_samples_without_epoch_field_load_as_epoch_one` | 旧记录兼容 |

## 对已有结果的影响

- manifest 每个任务新增 `epochs`，`cache` 新增 `namespace`；`harness diff` 比较新旧运行时会显示这些差异。
- compare 默认缓存策略由 `never` 改为 `always`（理由见第 1 节）。两份示例配置已同步修改。
- 多 epoch 运行的 `summary.ci95` 口径变为按样本重抽样；单 epoch 不变。

## 待真实验证与遗留

- 没有真实模型的多 epoch 运行；`--epochs` 和 `compare --resume` 的真实中断恢复需要在本地或通过外部 API 验证。建议命令：`harness run -c configs/ollama-smoke.yaml --epochs 3`，中途 Ctrl-C 后用同一命令重跑，确认已完成部分不再调用模型。
- 显著性检验只有符号检验与 bootstrap；没有多重比较校正，也没有按题目难度分层。
- `harness run` 本身没有 `--resume`：续跑会生成新的运行目录（依靠缓存避免重复调用），而不是在原目录中补齐。
- 失败报告只针对单次运行；对照目录还没有汇总的 Markdown 报告。
