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
| [11](11-stage5-review-and-next.md) | 审查已缓存的 PR #6～#10：创建失败清理、缓存身份、环境漂移；本地试运行与下一阶段 | 阶段 5 累积复评；07～10 的源码在远端分支快照中审查 |

想在自己电脑上跑起来，见 [上手练习指南](../hands-on.md)。
