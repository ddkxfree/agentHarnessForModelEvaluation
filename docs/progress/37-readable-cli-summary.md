# 37 · 终端汇总表可读性

日期：2026-10-29。用户反馈真实 DeepSeek 单题运行结束时，所有 outcome 横向排列导致表头被压缩为省略号，无法理解结果。

## 改动

- run 和 show 的公共汇总表改为两列“指标／结果”，长字段按行折叠，不再截成省略号；多个任务分区显示。
- 指标和结果类型增加中文解释，保留原英文名称便于与 JSON 归档对照。
- 默认显示样本数、准确率、95% CI、通过、未通过、缓存命中，以及所有非零的其他结果类型；说明未列出的类型均为 0。
- show 新增 --all-outcomes，展开包括零计数在内的全部结果类型。无需新增模型调用，也无需重新评测历史记录。
- 没有改变评分、统计口径、归档格式或执行循环。单题置信区间的解释与局限仍与之前相同。

## 验证

- 用 `runs/20261029-081524-deepseek-flash` 实际执行 show 默认及 --all-outcomes，两种显示均正常。
- 临时渲染检查覆盖 40／60／80 列宽、多任务、全部异常类型非零，中英文标签完整、无省略号。窄终端允许换行。
- pytest：352 passed、24 skipped（27.43 秒）；Docker 与可选依赖等受当前会话条件限制而跳过，没有重新进行真实模型或 Docker 实验。
- ruff check、ruff format --check 通过（128 files already formatted）。

## 用户复现

```bash
uv run harness show runs/20261029-081524-deepseek-flash
uv run harness show runs/20261029-081524-deepseek-flash --all-outcomes
uv run harness show runs/20261029-081524-deepseek-flash --sample terminal/hello-file
```
