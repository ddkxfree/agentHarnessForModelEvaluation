# 第57轮：运行进度与离线报告

日期：2026-11-06。

用户希望消除批量运行时终端像卡住的观感，并在不启动Harbor Viewer的情况下查看执行数据。

新增`presentation.py`：日志推断阶段、10秒心跳、最近工具活动、静默时长、并发阶段汇总；run与resume接入，清理和归档显示阶段提示。基于导入后的脱敏快照生成自包含report.html，支持轨迹、工具结果、得分用量、阶段耗时和日志。新增`harness harbor export`补导出历史运行。报告生成错误不影响评测结果。

既有Cursor运行报告已生成。真实Oracle运行`fix-git-oracle-20261106T081322Z-a9ca`验证0秒／10秒心跳、清理与归档提示、1/1正确、自动报告和无残留容器。无需模型调用。HTML内容转义、CSP禁止脚本与外联、路径／符号链接检查、已知凭据脱敏、缺失字段保持未知。

新增5项测试覆盖部分事件流、异常结束、心跳、HTML注入／脱敏／未知、缺失轨迹与符号链接。受限环境初测Harbor进程测试因ps权限失败，随后在允许ps的环境执行回归。全量含Docker测试等待外部依赖，在79 passed、4 skipped时温和中断；改跑非Docker回归。浏览器工具file协议被策略拒绝，未声称视觉验收通过。

使用说明见[进度与离线报告](../harbor-offline-reports.md)。

最终验收：非Docker回归479 passed、8 skipped（76.75秒），全仓ruff check／format及git diff --check通过。已生成真实Cursor报告约692KB，HTML解析核对15个步骤，无script／iframe／img／外部link标签。真实Oracle验证和报告生成不调用模型。
