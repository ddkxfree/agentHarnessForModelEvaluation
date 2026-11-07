# 多评测集运行计划

本轮核对 SWE-bench 官网、Scale Pro V2、Terminal-Bench 和 JobBench 的题集、计分及提交规则。按当前目标范围有 4 个维护体系、9 个公开榜单视图。SWE-bench 的 5 个主榜为 Full 2,294、Lite 300、Verified 500、Multilingual 300、Multimodal V2 480。Bash Only 是 Verified 的 mini-SWE-agent 筛选。Scale Pro V2 的两个视图是公开全量 642 和 Hard-51 子集。Terminal-Bench 当前完整集为 66，CPU 子集为 63。JobBench main 65 为榜单集，easy 63 单独记录。

| 阶段 | 预定范围 | 开跑前的验收 |
|---|---|---|
| 1 | Terminal-Bench 官方摘要，5 道预跑，之后 66 × 5 | 原版任务在目标沙箱的 Oracle、Cursor 固定版本、网络隔离、ATIF 和扣量 |
| 2 | SWE Lite 小批验证生成和重评，Verified 500 × 1 为主结果 | 匹配 base commit 和镜像，从 Cursor 导出单一补丁，用官方 harness 在干净容器复评 |
| 3 | Scale Pro V2 Hard-51 × 1，合格后扩展到全量 642 | 作答沙箱仅能访问模型端点，关闭远程搜题工具，50 分钟后保留补丁并在干净镜像重评 |
| 4 | JobBench main 5 道预跑，之后 65 × 1 | 独立 Harbor 0.22.0，原版交付物提取与 Grok 4.3 裁判端到端，裁判 Key 和费用独立 |
| 5 | SWE Lite、Multilingual、Multimodal V2、Full 以及 JobBench easy 分批补齐 | 分别复核提交资格、截图输入与官方评分入口。Full 最后，记录与 Lite、Verified 的实例重叠 |

先用 Cursor CLI 与 `grok-4.7-high` 标准速度。每个 Job 只跑一个固定题集与实验条件。按 task ID 和试次排队，记录镜像、版本、网络规则、预算与清单，重试保留原始尝试。预跑本机并发 1，确认资源和账单后再调。已读取本机下载的 66 个 Terminal-Bench 任务配置，全部作答上限 28,800 秒，3 道要求 GPU，9 道内存至少 16 GB。全量先落实合格沙箱。本机 CPU 子集可作为独立结果。

作答结束由 Agent 正常退出、预定上限或异常触发。区分测试未通过、Agent 超时／拒答、模型服务异常、环境或 verifier 失败、人工取消。保留日志、轨迹、补丁或交付物及评分。Terminal-Bench 错误试次按榜单口径计零，不能只挑成功的重跑。经典 SWE 的 Agent 上限与官方补丁判分超时分开。JobBench 默认作答两小时，缺失或失败的裁判结果必须解决才能报告完整主榜成绩。

Terminal-Bench 的原始 Job 可审查后发布 Harbor Hub，榜单入口在 Terminal-Bench 仓库。指南明确标注 4.0 社区投稿关闭，4.1 尚未确认重开。SWE 的 Lite、Verified、Multilingual 按官方说明打包补丁、测试日志和逐题轨迹，再向 SWE-bench/experiments 提交入口。Verified 与 Multilingual 有机构与研究材料门槛，Multimodal 有独立 sb-cli 路线。Scale Pro V2 和 JobBench 有公开榜单及复现材料，未核实个人自助投稿入口。独立报告注明条件及覆盖率，和官方排名分开。

首期完整目标为 330 + 500 + 51 + 65 = 946 次作答，另计预跑、Oracle、重评与裁判。每批对账 Cursor Models 的实际额度变化，记录输入、缓存与输出和并发下的时长。截图百分比不换算成硬性 Token 预算。本轮为规则核对与计划设计，未启动新试次或上传结果。

- [SWE 官网](https://www.swebench.com/) · [提交说明](https://github.com/SWE-bench/experiments) · [Checklist](https://github.com/SWE-bench/experiments/blob/main/checklist.md)
- [Scale Pro V2 榜单](https://labs.scale.com/leaderboard/swe_bench_pro_public_v2) · [锁定评测协议](https://github.com/scaleapi/SWE-bench_Pro-os/blob/main/v2/README.md)
- [Terminal-Bench 榜单提交](https://github.com/harbor-framework/terminal-bench/blob/main/leaderboard/SUBMIT.md)
- [JobBench 执行及评分](https://github.com/Job-Bench/job-bench-eval) · [Harbor 配置](https://github.com/Job-Bench/job-bench-eval/blob/main/harbor/README.md)
