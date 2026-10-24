# 进度记录

每完成一轮工作写一篇，说明做了什么、怎么用、怎么验证的、发现了什么、下一步是什么。

| 编号 | 内容 | 对应路线图 |
|---|---|---|
| [01](01-platform-core.md) | 平台内核：GSM8K 与 MMLU，并发、重试、缓存、manifest、rescore、diff | 阶段 1～4 |
| [02](02-agent-sandbox.md) | Agent 评测与 Docker 沙箱：基线 Agent、轨迹记录、3 个终端任务、Oracle 校验 | 阶段 5 前半 |
| [03](03-swebench-mini-swe-agent.md) | 接入 SWE-bench Lite 和 mini-swe-agent，用参考补丁校验判分 | 阶段 5 第 6、7 步 |
| [04](04-correctness-diagnostics.md) | 失败分四类、分数校验、数字精确比较、提交校验、CI 触发分支 | 阶段 5 加固第一轮 |
| [05](05-review-correctness-diagnostics.md) | 第一轮复评：NaN 漏判、长小数精度、重评状态；十阶段最小验收计划 | 阶段 5 复评与后续计划 |
| [06](06-review-fixes.md) | 修复 R1～R3：验证脚本拒绝 NaN、长小数精确比较、重评恢复评分故障 | 阶段 5 加固第一轮补修 |
| [07](07-traceability.md) | 失败可追踪：中途失败保留轨迹、单样本落盘、完整输出另存、清理失败记录、样本时限与线程取消 | 阶段 5 加固第二轮 |
| [08](08-independent-verification.md) | 独立复核：完整产物与哈希、镜像与平台身份、服务端模型标识、缓存策略、SWE-bench 新容器评分与 `harness verify` | 阶段 5 加固第三轮 |
| [09](09-terminal-bench.md) | 接入真实 Terminal-Bench 2.0：固定提交与镜像 digest 的 5 道题，按 Harbor 流程判分，Oracle 5/5、不操作 0/5 | 阶段 5 加固第四轮 |
| [10](10-solver-comparison.md) | Solver 对照：`harness compare` 固定条件重复运行，3B 模型下 BasicAgent 0.33、mini-swe-agent 0.11 | 阶段 5 加固第五轮 |
| [11](11-stage5-review-and-next.md) | 审查已缓存的 PR #6～#10：创建失败清理、缓存身份、环境漂移；本地试运行与下一阶段 | 阶段 5 累积复评；07～10 的源码在远端分支快照中审查 |
| [12](12-stage5-followup-fixes.md) | 补修 N1～N4：创建失败与取消时删除容器、缓存身份单独记录、compare／verify 报告实际环境漂移、mini 失败归因更正 | 阶段 5 补修 |
| [13](13-statistics-diagnostics.md) | 阶段 7 最小版：epochs 与缓存命名空间、compare 恢复、配对统计与置信区间、diff 原因提示、失败报告 | 阶段 7 |
| [14](14-stage7-review.md) | 阶段 7 复评：消融条件间缓存共享、恢复后的耗时口径、部分分数一致性 | 阶段 7 待补修 |
| [15](15-stage7-review-fixes.md) | 补修 S1～S3：条件间缓存隔离、回放与推理指标分开（分数／性能可比性）、部分分数的波动统计 | 阶段 7 补修 |
| [17](17-stage10-review.md) | 阶段十分支复评：预算并发边界、实验归因、可选依赖测试；真实消融待验证 | 阶段 10 待补修；16 在阶段十分支中审查 |

想在自己电脑上跑起来，见 [上手练习指南](../hands-on.md)。
