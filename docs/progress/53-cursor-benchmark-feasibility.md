# 第53轮：Cursor订阅批量做公开评测题的可行性

日期：2026-11-05。

本轮按用户要求只做调研，报告见 [Cursor批量评测可行性](../cursor-benchmark-feasibility.md)。核对Cursor官方CLI、订阅池、认证方式，检查本机Harbor0.25.0与JobBench独立Harbor0.22.0均包含cursor-cli适配。确认SWE补丁评分、TB隔离任务、JobBench文件交付与独立LLM裁判的三条路径。

结论：可使用订阅模型作为Cursor Agent批量作答，外部模型／Judge API不会因此自动由订阅支付；JobBench main65题与easy63题应分开。提出628题范围及条件化token／工期估算，先5题端到端验收再扩大。

验证方式为官方文档、源码和本机包元数据只读检查；未登录Cursor、读取密钥、发起模型／Judge请求、运行容器或下载新数据。当前CLI兼容性、账户扣费、TB4.1支持和全量资源仍待实测。本轮仅文档变更，不运行代码测试。
