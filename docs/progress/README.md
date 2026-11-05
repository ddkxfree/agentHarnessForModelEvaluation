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
| [16](16-budget-ablation.md) | 阶段 10 最小版：单样本 token 预算、Solver 协议记录、compare 区分预算敏感性与机制消融；真实消融配置已备好，待本地／API 运行 | 阶段 10 |
| [17](17-stage10-review.md) | 阶段十分支复评：预算并发边界、实验归因、可选依赖测试；真实消融待验证 | 阶段 10 待补修；16 在阶段十分支中审查 |
| [18](18-stage10-review-fixes.md) | 补修 T1～T3：预算下并发调用串行化、请求的实验类型与记录支持的解释分开、可选依赖缺失时只跳过 mini 用例；阶段十合入 master | 阶段 10 补修 |
| [19](19-stage10-fix-review.md) | 阶段十补修复评：T1／T3 通过，T2 尚漏任务内额外差异；阶段六最小 Judge 验收入口 | 阶段 10 复评与阶段 6 计划 |
| [20](20-stage10-t2-fields.md) | T2 小补修：任务条目逐字段检查（生成参数、评分器、数据集变化不再被归因），条件解释纳入整体原因 | 阶段 10 补修 |
| [21](21-judge-minimal.md) | 阶段六最小 Judge：rubric 评分、A/B 双序比较、双裁判聚合、完整归档与离线重解析、校准入口；真实校准待验证 | 阶段 6 |
| [22](22-stage6-review.md) | 阶段六复评：主体与现有测试通过；后处理输入指纹、校准阈值与标签校验待补修 | 阶段 6 待补修 |
| [23](23-stage8-development-guide.md) | 阶段八开发交接：生成、验证、反馈修复、候选 diff 与人工评审；工作流和真实接入分别验收 | 阶段 8 计划 |
| [24](24-stage6-review-fixes.md) | 补修 J1、J2：裁判输入身份不含后处理，阈值等可用 `rescore -p` 离线重评并记录历史；校准阈值与全部标签先校验，报告覆盖率 | 阶段 6 补修 |
| [25](25-onboarding-agent.md) | 阶段八第一版：接入规格、隔离候选工作区、独立验证器、Agent 生成／验证／反馈修复循环、预算、归档与候选 diff、重新验证；真实接入待验证 | 阶段 8 |
| [26](26-post-ten-plan.md) | Cursor 开发额度下的双线推进：本地试运行、阶段八与操作体验开发、后续扩展优先级 | 试运行与后续计划 |
| [27](27-onboarding-review.md) | 冲突后 README 整理；Judge 补修回归通过；阶段八执行隔离、字段映射与总时限待补修 | 阶段 6 补修复核与阶段 8 复评 |
| [28](28-onboarding-review-fixes.md) | 补修 O1～O3：候选代码默认在无网络、只读根文件系统的容器中验证；冻结预期样本检查字段与模型实际看到的题目；总时限覆盖模型等待、验证和自测，取消时 kill 检查进程／容器 | 阶段 8 补修 |
| [29](29-onboarding-fixes-review.md) | O1 默认容器隔离已实现、Docker 实测待复核；O2 回归通过；O3 原误报成功已修，镜像准备／容器启动仍漏总时限 | 阶段 8 补修复评 |
| [30](30-onboarding-o3a-fix.md) | 补修 O3a：镜像准备独立限时、在尝试计时前完成并记录，构建在可 kill 的子进程中；容器创建／启动／等待共用剩余时间，迟到的容器被删除；在 Docker 上复核全部隔离回归 | 阶段 8 补修 |
| [31](31-onboarding-o3a-review.md) | O3a 时限主路径与现有回归通过；取消与创建结果交接仍可能启动容器（P2 O3b）；本机 345 passed、24 skipped | 阶段 8 补修复评 |
| [32](32-onboarding-cancel-race-fix.md) | 补修 O3b：每次交接都在 cancel 的同一把锁下检查取消，已取消执行器不再构建、创建、启动或等待；取消单独记为 `cancelled` | 阶段 8 补修 |
| [33](33-stage8-acceptance-and-first-run.md) | O3b 原复现已阻止；本机 352 passed、24 skipped，进入环境检查、Oracle、真实小运行与实验验收；补齐遗漏的第 32 轮简要记录 | 阶段 8 复评与试运行 |
| [34](34-first-run-plan-and-environment.md) | 制定模型／Agent 到 TB、SWE、JobBench 的路线；Docker 已启动，基础镜像拉取未完成，Oracle／no-op 尚未验收，无真实模型调用 | 实际试运行 |
| [35](35-docker-proxy-and-oracle.md) | Docker 配置 7897 代理、Python 官方镜像拉取与运行成功；三个自建任务参考解全过、不操作全失败，2 个 Docker 测试通过，无遗留评测容器 | 首次无模型环境验收通过 |
| [36](36-stage11-20-design.md) | 阶段 11～20 设计：诊断、回归、故障注入、Docker SRE、任务生成与受控优化的分批开发；Cursor 启动提示词与到期前收口范围；仅文档设计 | 后续研究能力与平台扩展计划 |
| [37](37-readable-cli-summary.md) | run／show 改为中英文两列汇总，非零异常自动显示，--all-outcomes 展开零计数；窄终端完整换行 | 终端操作体验 |
| [38](38-fix-git-token-limit-analysis.md) | 真实 TB fix-git：找到丢失提交、merge 冲突未处理，30,032 tokens 耗尽预算；核对用量与 shell 退出状态，建议单变量预算对照 | 真实模型失败分析 |
| [39](39-harbor-integration-plan.md) | Harbor 双入口正式交接：固定发行版、隔离 CLI、G0～G6 连续自验、统一结果与生命周期；只完成计划，待 Cursor 实现 | 开发计划 |
| [40](40-harbor-development-bounds.md) | Harbor 开发防空转：单批时限、重试／等待上限、进程退出与资源清理、恢复不重置预算、到限草稿交付；区分操作约束与账户硬限额 | 开发计划补充 |
| [41](41-harbor-backend.md) | Harbor 0.25.0 双执行入口：独立 venv＋哈希锁、doctor／run／import／status／cancel／resume，统一只读视图；真实 nop、取消、恢复通过，Oracle 满分因本机 compose 网络无出网未验收 | 工程集成，待评审 |
| [42](42-harbor-backend-review.md) | PR #25 已合入后独立评审：H1～H7 待补修；macOS 孤儿取消、启动前取消、缺失样本分母和日志凭据可复现；409 passed、2 failed、10 skipped | Harbor 补修评审 |
| [43](43-harbor-review-fixes.md) | H1～H7 补修：跨平台进程身份与未知时拒绝、带时间戳的取消在锁内核对、缺失 Trial 计入分母并警告、日志与原生文件脱敏且原件只留 local/、清理单一期限、零预算与 delete=false 拒绝；439 passed、4 skipped；真实 Oracle／nop 待本机 | Harbor 补修，待复评 |
| [44](44-harbor-fixes-review.md) | PR #26 复评：H1a macOS 退出探测异常、H3a 旧归档未重建、H4a 非文本日志绕过脱敏待补；讨论 DeepSeek 小规模实验与只读网页展示 | Harbor 复评与展示方向 |
| [45](45-harbor-review44-fixes.md) | H1a 进程组存活由成员列表判定、EPERM 不当已退出，停不下时记 failed 不写 cancelled；H3a 导入器升到 2、旧归档警告与 `harbor rebuild`；H4a 非 UTF-8／NUL／二进制逐字节脱敏；454 passed、4 skipped | Harbor 补修，待复评 |
| [46](46-harbor-review44-fixes-review.md) | PR #27 复评：macOS Harbor 专项 91 passed、5 skipped；原三项主路径通过，旧快照 rebuild 脱敏迁移 H4b 和未提交的第 45 轮文档 D1 待补 | Harbor 补修复评 |
| [47](47-harbor-review46-fixes.md) | H4b 快照脱敏版本单独记录，rebuild 优先从 local/ 原件重导入、否则重新脱敏旧快照（保留原始哈希、更新存储哈希），旧快照标“不可分享”、迁移后标“分享前审核”；D1 补交进度 45；459 passed、4 skipped | Harbor 补修，待复评 |
| [48](48-web-app-prototype.md) | `frontend/` 六模块演示原型：工作台、评测集、实验中心、轨迹分析、实验对比、模型与连接；不调用模型、不连接后端 | Web App 原型 |
| [49](49-web-report-navigation.md) | 总览／报告／说明分层，独立日志与证据链接，本地持久化及跨标签页读取；10 项前端测试通过 | Web App 展示优化 |

第 [47](47-web-app-prototype-plan.md) 轮：Web App 六模块原型设计与 Cursor 提示词，仅文档设计。实现见 [48](48-web-app-prototype.md)。

想在自己电脑上逐步跑起来，先读 [逐步运行计划](../first-run-plan.md)；旧的 [上手练习指南](../hands-on.md) 仍有待修订内容。
