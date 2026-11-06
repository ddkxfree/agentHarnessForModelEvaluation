# 第54轮：Cursor轨迹与重试可观测性核查

日期：2026-11-05。继续只读检查本机Harbor0.25.0 Cursor适配、TrialQueue与Job恢复代码，并核对Cursor官方输出格式。

确认原始流与ATIF轨迹可以保留公开的作答过程；token仅在结束事件提供usage时汇总，未实现逐步token。CLI内部请求重试不具有完整可见性。发现Harbor内部异常重试删除旧trial目录，恢复时也清理没有result.json的目录，不能声称所有失败attempt默认永久保存。

将结论及外层独立attempt归档建议补入[调研报告](../cursor-benchmark-feasibility.md)。没有修改执行器或调用模型，没有实测具体CLI输出；文档差异检查通过，未运行代码测试。
