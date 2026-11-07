# Terminal-Bench 榜单协议与 Cursor 预检

首个正式条件拟用 Cursor CLI + `grok-4.7-high`（标准速度）。固定 Harbor、Cursor CLI、模型 ID、思考档位、题库摘要及执行环境。Fast 作为独立条件记录。此前派生 `fix-git` 的成绩只用于验证运行链路。

榜单当前校验器锁定 `terminal-bench/terminal-bench@sha256:39d9f44b40420cde8fdcc087579c0d72a7e14fa3656d603c3f0d22fb35e27732`，要求 66 题各至少 5 次，共至少 330 次独立试次。允许将同条件的完整批次合并。保持官方题目摘要、默认超时和资源设置；每次从干净的任务环境和新会话开始，保留全部结果及异常。公开记录必须能回溯至 Harbor Hub 的原始 Job 和 Trial，每个成功试次需要 ATIF 轨迹接受审查。正式指标由官方校验器计算，错误计零。

本机已验证 Harbor 0.25.0 有 dataset、并发、试次数及上传参数，Cursor CLI 适配可生成 ATIF。首次下载远端断开，重试后官方固定摘要的 66 道题全部导出至 `runs/tb-leaderboard-preflight/`。正式运行仍应引用官方 Registry 摘要，保留题库来源。当前进程缺少 Cursor API Key，尚未开始作答或对外发布。

下一步先确认原版任务、镜像及网络隔离。按官方 README 建议在目标沙箱跑 Oracle，正式前用少量固定题验证实际 Agent 版本、用量归属、ATIF 和异常记录。全量包含 GPU 等环境要求，本地 CPU 子集另报。试跑前核对按量付费设置，按实测扣量和用时估算全批。

榜单指南仍标注 4.0 社区投稿关闭，尚未确认 4.1 已开放。上传 Hub、提交榜单和贡献任务修复是不同路径。公开前审核轨迹、配置与产物中的私有信息，并复核官方投稿入口。上游要求 PR 描述与 issue 报告由人手写。

- [官方提交指南](https://github.com/harbor-framework/terminal-bench/blob/main/leaderboard/SUBMIT.md)
- [榜单固定题库](https://github.com/harbor-framework/terminal-bench/blob/main/leaderboard/src/leaderboard/core/hub.py)与[静态校验](https://github.com/harbor-framework/terminal-bench/blob/main/leaderboard/src/leaderboard/ci/static_analysis.py)
- [4.1 发布说明](https://github.com/harbor-framework/terminal-bench/releases/tag/v4.1.0)与[上游贡献要求](https://github.com/harbor-framework/terminal-bench/blob/main/AGENTS.md)
