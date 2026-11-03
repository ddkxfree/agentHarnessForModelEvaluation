# 44 · Harbor 补修复评与实验展示方向

日期：2026-11-03。评审基准 master `a907ef3`（PR #26 已合并，补修分支末端 `c09a739`），开始时工作区干净。**结论：上一轮修复主体有效，H2、H5、H6、H7 的相关回归通过；H1、H3、H4 仍各有一个可复现残留，需要小补修。**本轮只更新文档，没有改实现、安装 Harbor 或调用收费模型；额外基础回归触发了 onboarding Docker 检查，环境失败后中止，没有完成真实 Docker 验收。

## H1a · P1：macOS 孤儿进程取消在退出探测时抛 PermissionError

位置：`src/harness/integrations/harbor/process.py:484` 的 `_stop_group()`。

新的 ps 身份路径能在允许 ps 的 macOS 环境识别活进程，未知身份拒绝恢复也已实现。但孤儿取消没有 Popen 对象，SIGTERM 后以 `os.killpg(pgid, 0)` 判断进程组是否存在；只捕获 ProcessLookupError。本机可复现：本次独占子进程收到 SIGTERM 后、父进程尚未 wait 回收时，探测返回 PermissionError(errno=1)，wait 回收后才返回 ProcessLookupError(errno=3)。因此 cancel 抛异常，后续容器清理、状态落盘和发布均未执行。

已在允许 ps 的执行环境重跑定向用例，`test_status_and_cancel_of_an_orphaned_harbor_process[ps]` 稳定失败于该行，不能归因为原沙箱禁止 ps。独立的最小子进程实验得到 `group probe before reap: PermissionError 1`、`child wait: -15`、`group probe after reap: ProcessLookupError 3`；所有测试子进程已终止。

补修验收：妥善区分权限不足、已退出／僵尸与仍存活的组成员；不能把所有 EPERM 一律视为已退出，也不能因此跳过有时限的自有资源清理与状态记录。覆盖未知权限与 macOS SIGTERM 后的这一行为；现有本机孤儿取消用例应通过。

## H3a · P1：导入器版本未变，旧归档继续保留错误统计

位置：`src/harness/integrations/harbor/importer.py:32`、`:519` 的幂等判断。

本次新增计划 Trial 占位和分母修复，但 IMPORTER_VERSION 与 MAPPING_VERSION 仍都为 1。重新导入相同源文件时，新记录虽然已算出，最终因原始索引和版本相同直接返回 unchanged，连 replace=True 也无法更新既有 trials.jsonl。

复现使用 `git show c3540ec:src/harness/integrations/harbor/importer.py` 取得上一实现，在临时目录导入同一合成 Job（计划 2 个，发现 1 个且 reward=1），再用当前实现重导入：

```text
旧实现首次导入：n=1, accuracy=1.0
当前实现 replace=True：action=unchanged, n=1, accuracy=1.0
当前实现导入全新目录：n=2, accuracy=0.5
```

这会直接影响用户已有归档及后续网页展示。补修验收：提升导入／映射身份并提供明确重建路径，增加“旧版本归档 → 当前版本重导入”的回归；旧错误记录必须更新或提示重建，不能以 unchanged 掩盖。明确 replace 的强制重建或版本冲突语义。

## H4a · P1：非 UTF-8／含 NUL 的日志绕过凭据脱敏

位置：`src/harness/integrations/harbor/importer.py:139` 的 `redact_bytes()`；`process.py:662` 将其用于公开日志。

新的 local/ 私有边界和普通 UTF-8 日志脱敏已有效。但 redact_bytes 一遇 NUL 或 UTF-8 解码失败就原样返回，没有先替换已知秘密。工具输出可能混合非文本字节，不能据此将其当安全产物公开。

仅使用合成标记：给 local/harbor.raw.log 写入 `b'\xff diagnostic ' + marker.encode() + b'\n'`，再调用 `_publish_log(run_dir, [marker])`；生成的可分享 harbor.log 仍含完整 marker。导入原生文件也走同一函数，有相同问题，没有使用真实密钥。

补修验收：对任意字节先处理已知秘密，或将不能可靠脱敏的内容限制在私有边界并在公开索引注明省略；日志可采用可控的解码／字节替换策略。覆盖无效 UTF-8、NUL、已知秘密在前后不同位置及公开原生快照。不能只是让测试避开这类字节。

## 验证口径

本地受限沙箱禁止 ps，因此第一次基础回归出现 7 failed、422 passed、14 skipped（48.61 秒）；不能把这七项全算成代码缺陷。随后在允许 ps 的环境重新检查：Harbor 专项为 **1 failed、76 passed、4 skipped**（21.50 秒），唯一本机失败是 H1a；四个跳过为没有 Linux /proc 的参数组。单独重跑 H1a 仍失败。

另一次允许 ps 的基础回归排除了 tests/test_docker_sandbox.py，但 tests/test_onboard.py 也含 Docker 用例，本机 Docker 可用后触发真实环境准备。无输出数分钟后向本次精确测试进程发送 SIGINT，最终为 **2 failed、255 passed、8 skipped，223.73 秒，中断退出**，不是完整回归通过。两项失败为 H1a 和 `test_docker_blocks_adapter_writes_outside_its_scratch_directory`（期望 import_error，实际 env_error；本轮不将其归因为新 Harbor 代码）。此前观测到的测试子进程已退出，只读检查未发现带 harness.onboard=check 标签的遗留容器。

静态检查：ruff check 通过，ruff format --check 为 149 files already formatted。H3a／H4a 是额外定向复现，现有测试未覆盖；因此即使其余套件通过，也不能认定两项已修好。真实 Harbor Oracle=1／有效 nop=0 与 Native Docker 回归仍待补，不因本轮静态评审而改变状态。

## 下一步：小补修、真实实验与只读网页并行

用户希望先用 DeepSeek 建立可以放在简历上的网页实验展示。这个方向与项目当前状态相符；此处记录讨论方向，不表示网页或新 Benchmark 已实现，也不授权本轮开始收费批跑或发布网站。

建议顺序：

1. Opus 在独立补修分支解决 H1a／H3a／H4a，Codex 复评；已有原始运行保留，按新导入身份重建视图。
2. 同时开发只读页面，先用历史 Native 结果与明确标注的 Harbor 测试样例验证展示，不把样例混入真实成绩；页面不直接执行 shell 或启动实验。
3. 用户本机补 Harbor Oracle／nop 与 Native Docker 环境验收，随后以 DeepSeek 做一个 Harbor Agent 的单题兼容性检查。
4. 先固定 Terminal-Bench 已接的 5 题，再做 SWE-bench 单题及小子集；JobBench 尚未接入，需确认具体官方项目、交付物与评分协议后再加，不能在页面上填成绩。
5. 每个系统先各跑一次，确认成本和链路；需要对照时，在同一固定子集、相同模型版本与预算协议下增加重复实验。环境错误、预算耗尽和任务失败分别呈现，Oracle 不计为模型成绩。

页面每条实验至少有：Benchmark／版本／题目集合、模型／实际返回版本与日期、Agent／版本、Native 或 Harbor 执行来源、预算与重复、成功数／总数／完成率、用量／费用来源、耗时、失败类型、轨迹与脱敏证据。不同 Benchmark 分页展示，不拼成未经定义的总分；BasicAgent 与 Terminus-2 的结果是系统对照，不归因为纯执行器差异。

首版做实验总览、任务明细与轨迹、配置差异／失败案例三个视图即可。公网版本只发布审核后的脱敏导出，不暴露整个 runs/ 或 local/；费用未知就展示未知。后续再考虑排名、调度或数据库，不以这些作为第一版前提。

截至本轮核对，[DeepSeek 官方定价](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)显示空闲价格为高峰一半：北京时间工作日（不含中国法定节假日）9:00–12:00、14:00–18:00 为高峰，其余为空闲。deepseek-flash 空闲时段每百万 tokens：缓存命中输入 0.02 元、未命中输入 1 元、输出 4 元。仅用于当前讨论；实际实验应记录价格时间、命中用量和估算／实付区别，不能将今天价格回填成历史实付。夜间成本更低不代表应无预算批跑，Harbor 当前仍不支持强制 token／费用上限。
