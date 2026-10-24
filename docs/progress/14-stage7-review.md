# 14 · 阶段七文档与实现复评

日期：2026-10-24。评审基准：`8ebc843`（包含阶段七实现 `68c1d00` 和进度文档 PR #14）。

本次工作区已更新到阶段七；上一轮评审时停在 PR #6 的状态不再适用。本轮只做源码检查、MockModel 复现和文档更新，没有修改实现、启动模型或调用付费 API。

## 结论

progress/13 对完成范围、MockModel 测试、真实运行待验证和 run 没有原目录续跑的限制写得清楚。按样本重抽样、复用 compare、按 epoch 隔离缓存和离线失败报告的方向合理。

阶段七主体已实现，但完整验收仍需补两个缓存／耗时语义问题，以及一个部分分数统计边界。补修后可以进入阶段十，不必等待云端 CPU 模型评测。真实模型验收继续保留为本地／API 队列。

## 本机验证

- 当前 HEAD：`8ebc843`，分支 master。
- `UV_CACHE_DIR=/private/tmp/harness-review-uv-cache uv run --no-sync pytest -q -rs`：**198 passed、16 skipped**。
- ruff check 和 ruff format --check 通过（79 files already formatted）。
- Docker 不可用，`minisweagent`、`swebench` 可选依赖缺失，相关模块与集成测试跳过。没有独立复核文档中的全部 231 passed。
- 下面三个问题都已在本机复现，均无需真实模型。

## S1 [P1] 不同消融条件共享请求缓存，默认实验语义不完整

位置：`src/harness/analysis/compare.py:199-223`。每个条件使用相同的 spec.cache_namespace 与 epoch，只按 epoch 隔离，没有条件维度。

真实 BasicAgent 的 max_steps 不进入模型消息。同一个样本在 max_steps=1 和 max_steps=2 两种条件下，第一轮请求相同，后一个条件会读前一个条件刚写的缓存。若前者已经结束，后者甚至完全不调用模型。

复现配置：terminal/hello-file，一个样本，max_steps 1 对 2，repeats=2，默认 cache_policy=always；使用 MockModel 和 FakeProvider。

```text
独立执行两个条件、两个 epoch 应调用模型：4 次
实际调用：2 次
base.cached_outputs：0
longer.cached_outputs：2
comparable：True
```

这不是“不同 epoch 共享”的问题，而是条件间共享。共享轨迹前缀可以设计成一种明确的实验模式，但当前文档没有声明这种耦合；尤其不能把缓存重放带来的耗时降低解释为某个机制更快。

验收：默认请求命名空间包含实验、条件和 epoch；同一条件／epoch 恢复仍可重放，不同条件默认不复用。测试应使用提示词相同但执行参数不同的消融，而不能只用提示词不同的两个 Solver。若保留显式的共享响应实验模式，须保存策略、说明其解释范围，并排除实际推理耗时比较。

## S2 [P2] 恢复后的缓存耗时与首次推理耗时混合，报告未区分指标可比性

位置：`src/harness/analysis/compare.py:155-187`、`242-260`、`349-423`。

恢复会重新运行中断条件，并从缓存取得已完成的模型响应；但 wall_time_s 是本次重放的耗时。其他条件的旧运行仍保留原始推理耗时，汇总继续给出 mean_wall_time_s 与 comparable=True。

本机沿用现有测试的中断模拟：完成 2×2 对照，把 plain#2 标为 interrupted，再 resume。

```text
恢复期间模型调用：0 次
plain 两次运行的 cached_outputs：[0, 4]
careful.cached_outputs：0
comparable：True
```

分数仍可用于已定义的响应重放比较；问题是结果完整性、分数可比性与实际耗时／成本可比性不能共用一个无解释的布尔值。当前 token 总数也包含历史缓存响应的逻辑 token，不能直接当作恢复时新消耗的 API token。

验收：分别给出结果与性能指标的可比性；记录 fresh／cached／mixed 状态、恢复来源，区分逻辑 token 与本次调用 token。混合缓存运行不能作为无缓存推理速度比较。文档应说明请求级重放仍会重新执行工具、创建沙箱和评分，不等同于跳过已完成样本的完整 checkpoint 恢复。

## S3 [P2] 部分分数相同也被计为跨 epoch 不一致

位置：`src/harness/analysis/stats.py:87`。

目前用 0 < sum(scores) < len(scores) 判断 inconsistent_samples，只对二值分数成立。项目的 Score 支持 0～1 的部分分数，后续 Judge 也可能用到。

复现：同一题三个 epoch 的 score 均为 0.5。

```text
accuracy_per_epoch：0.5 / 0.5 / 0.5
std：0.0
inconsistent_samples：1
```

验收：明确指标含义。如果表示分数波动，应比较各 epoch 分数是否相同；如果表示成功／失败不一致，应根据 outcome 判断并采用相应名称。覆盖恒定部分分数、变化部分分数、恒定 0／1、交替成功失败。

## 文档应补充的边界

1. 第 1、2 节：补上条件间的缓存隔离与恢复后的指标口径，修正“新对照会重新采样”的解释范围。
2. 统计解释：ci95_over_samples 表示在已观察的逐题平均结果上按题目重抽样，不等于服务端随机性、未来运行结果或全部不确定性的区间。30 题只是展示策略阈值，不是达到该数量就能下结论的统计保证。
3. 文档给出的中断重跑验收命令应固定独立 namespace，明确依赖缓存可用与策略允许；新一次独立实验使用新的 namespace。
4. roadmap 顶部“当前工作区仍在 PR #6”的说法已经过期，应作为历史评审记录保留，不能继续描述当前代码。

## 下一批给 Opus 的范围

只补 S1～S3 与对应回归测试、文档说明。继续使用 MockModel／FakeProvider，不在云端启动 Ollama 长任务。补修后复评，再进入阶段十的 token 预算与一次真实单变量消融；真实消融在本地或外部 API 上执行，状态如实记为待验证。
