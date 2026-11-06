# 第52轮：三类评测题库与缓存策略调研

2026-11-05。按用户要求核对SWE-bench、Terminal-Bench、JobBench官方站点、论文、发行说明与任务清单。

报告见[题库调研](../benchmark-landscape-research.md)。明确Verified与Scale Pro为不同来源，Pro V2公开642／Hard51与V1私有构成分开；核实TB官网首页4.0与仓库最新v4.1.0差异，实际读取两个固定清单均66题，CPU-only63题；JobBench论文130与当前65+63=128口径不同，原因未证实。

说明运行时隐藏测试不等于私有题库，SWE镜像base/env/instance共享层与按需缓存，建议文本目录全缓存、镜像按批次准备。原始TB清单和tag/commit证据存于runs/benchmark-research-20261105/。

仅文档与只读查询，检查变更格式；没有模型调用、新镜像下载或自动升级现有固定环境。
