# 用 Cursor 订阅批量完成公开 Benchmark：可行性调研

日期：2026-11-05。范围：查阅官方文档、上游代码和本机安装包；未调用模型、读取凭据、下载新题库／镜像或启动评测。

## 结论

可行且有实际评测价值：用 Harbor 调度隔离任务，让 Cursor CLI 使用订阅内模型完成任务，再由评测集的验证器判分。被测对象是 Cursor Agent＋所选模型，不是裸模型。官方支持 CLI 自动化，无须模拟点击编辑器或把订阅逆向转成通用模型 API。

不能保证九天内跑完所有版本，也不能保证一轮消耗几十亿 token。应先指定公开版本、试跑核对扣费池与耗时，然后分批全量执行。

## 证据与接口

- [Cursor CLI 官方介绍](https://cursor.com/blog/cli)：CLI 使用 Cursor 订阅，支持终端／远程并行运行。
- [CLI 认证](https://cursor.com/docs/cli/reference/authentication)：支持账号登录和 Dashboard 创建的用户 API key；自动化可使用 `CURSOR_API_KEY`。这是 Cursor 身份凭据，不是另购的外部模型供应商 key。
- [旧套餐说明](https://cursor.com/docs/account/pricing/legacy-cursor-plans)：用户截图对应双月度池模式；Cursor Models 与 Other Models 分开，第三方模型和命名第三方子代理可能消耗 Other Models。应固定账户当前可用的 Composer／Cursor Grok 型号，不依赖 Auto；用量池归属必须由小批次账单核对。
- [Harbor Cursor 适配源码](https://github.com/harbor-framework/harbor/blob/main/src/harbor/agents/installed/cursor_cli.py)：已有 `cursor-cli`、模型选择、非交互执行、流式日志与轨迹转换。
- 本机实际检查 `runs/harbor-venv` 的包元数据为 Harbor 0.25.0，`runs/offline-assets-20261105/jobbench-harbor-venv` 为 0.22.0；两者均存在 `harbor/agents/installed/cursor_cli.py`，读取 `CURSOR_API_KEY` 并运行 `cursor-agent`。不是仅根据最新版文档推断旧版支持。未实际登录或验证当前 CLI 与适配器的运行兼容性。
- 适配器在 CLI 提供 usage 时收集 token；有的费用由价目表估算，不能当作实际账单，也不能把缺失用量记成真实零消耗。

## 三套题目的执行方式

| 数据集 | 建议明确的范围 | 作答与评分 |
|---|---|---|
| SWE-bench | Verified 500；若选择经典 Full，另立清单；不把重叠子集相加 | Cursor 在匹配 base commit 的环境修复代码，导出 patch，再用官方 harness 在干净环境评分 |
| Terminal-Bench | 4.1 全部66题，或官方 CPU-only 63题 | Harbor＋cursor-cli；官方 verifier 判分；CPU-only也需要逐题核查资源 |
| JobBench | main65题；easy63题单列 | 官方 Harbor 路线支持原生／自定义 agent；保存文件交付物后运行官方提取与LLM裁判 |

依据：[SWE Verified](https://www.swebench.com/verified.html)、[SWE评分指南](https://www.swebench.com/SWE-bench/guides/evaluation/)、[TB4.1发布](https://github.com/harbor-framework/terminal-bench/releases/tag/v4.1.0)、[JobBench说明](https://github.com/Job-Bench/job-bench-eval)、[JobBench Harbor指南](https://github.com/Job-Bench/job-bench-eval/blob/main/harbor/README.md)。

SWE 不要求生成端使用特定模型 API：官方评分入口接受 instance_id、model_name_or_path、model_patch。因此即便 Harbor 的某一 SWE 转换版本不满足原协议，也能用 Cursor 生成补丁后交回官方 harness；转换路径仍需另行核验。按批准备镜像，不先拉完所有版本。

JobBench 官方普通 CLI runner 未列 Cursor，但其 Harbor 路线可选择 Harbor 原生 agent；本机0.22.0已有Cursor适配，组合有代码依据，端到端仍待验收。官方默认裁判为 xAI grok-4.3，单独需要 `JUDGE_API_KEY`；Cursor 作答额度不能直接支付这一外部API。可以先生成归档后评分，但未经评分不能称完成正式评测。用Cursor自己判分会改变官方协议。

本机已有JobBench main/easy材料、5题TB2.0与2题SWE缓存，详情见 `offline-assets.md`。已有缓存不代表TB4.1就绪；4.1使用新的锁文件／验证器Workers，需要独立验证与固定Harbor0.25.0的兼容性，不升级覆盖历史运行环境。

## 用量与工期的估算

以 Verified500＋TB CPU63＋JobBench main65，共628次作答为例：若每题累计输入、缓存与输出合计分别为10万、50万、100万token，一轮对应6280万、3.14亿、6.28亿token。这些只是算术场景，不是实测预测；不同token类别对额度的消耗不同。

若平均每题30分钟，628题需314个执行小时，4并发理想约78.5小时；未计环境准备、裁判、排队、限流、失败重试与资源争用。需要试跑分别测量模型等待与容器运行时间，不能用模型输出速度直接推算总工期。

## 建议执行顺序与验收

1. 首批SWE2题、TB2题、JobBench1题，每题新容器／新会话，使用真实题目，不新增自制benchmark。
2. 确认Cursor模型标识、CLI版本、账号认证、扣费池、日志、作答产物和验证器都正常；认证信息只注入相应执行器，不写入公开归档。
3. 根据实测制定并发、磁盘、单题超时和账户额度停止策略；关闭非预期按量付费。沿用所选基准的时间／资源协议，改动必须记录。
4. 冻结清单后分批覆盖全量。记录失败、未运行、评分故障，不静默漏题。首轮得分锁定；修复后重做或第二模型另算独立实验。
5. 评分资料、参考补丁、oracle不进入作答工作区；联网按对应基准协议执行，防止检索到公开答案。公开集成绩不能证明没有训练污染。

是否合理：适合把现有平台变成有真实数据的跨基准评测系统。可以用重复运行估计稳定性，或比较Cursor池内两个模型；不要为了消耗量把多轮最佳答案混进首轮成绩。结果分别报告三套基准，不平均成无定义的总分，不标为官方认证成绩。

## 尚未验证

用户账户实际可用模型与剩余额度、CLI登录、账户并发限制、Cursor端到端执行、TB4.1兼容性、SWE全量资源、JobBench官方裁判费用均未实测。此报告确认技术路径有官方接口和本机适配代码支撑，不宣称全量已经可一键运行。

## 补充：轨迹、token与失败重试的可见范围

2026-11-05继续检查本机Harbor0.25.0源码和[Cursor输出格式文档](https://cursor.com/docs/cli/reference/output-format)。

- `cursor_cli.py` 使用 `--print --output-format=stream-json`，stdout与stderr合流保存到`agent/cursor-cli.txt`；转换后另写`agent/trajectory.json`。可记录CLI公开的消息、工具参数与结果、部分时间戳和调用关联。不是完整模型HTTP请求记录，不保证完整系统提示、压缩上下文或内部推理。
- 官方文档说明print模式抑制thinking事件。适配器虽然能解析thinking，不能由此推断实际运行能获得思维链。
- `CursorResult.usage`可空；只有result事件的usage进入final_metrics，含input/output/cacheRead/cacheWrite。构造Step时没有写入逐步token。官方当前示例也不承诺usage字段。因此总量有条件可得，每步精确token当前不可保证，不能拿总量平均分摊冒充实测。
- `llm_call_count=1`是适配器构造步骤时赋值，不是服务端请求审计；无法据此统计底层API重试次数。CLI内部网络重试、服务端重试、隐藏路由等，仅在CLI确实输出时才能看见，不属于Harbor可完整控制范围。
- 工具失败后Agent再执行命令，若CLI发出对应事件，可在轨迹中分析。Harbor整题异常重试是另一层：`trial/queue.py::_execute_trial_with_retries`在重试前调用`shutil.rmtree(trial.paths.trial_dir)`，失败attempt原目录默认被删除；`job.py`恢复任务也会删除缺少result.json的目录。故不能承诺原生配置永久保留所有尝试。
- 大规模运行前建议禁用内部自动重试，以外层独立attempt目录记录每次重跑，持久化原始事件与退出信息，再做关联汇总；中断日志需在恢复清理前归档。此方案尚未实现。费用估算、CLI报告用量与账户扣费应分别存储，缺失值标unknown，不能误记为0。

验收应包括正常完成、命令失败后恢复、超时／中断、整题重跑四种情况，并核对原始事件与转换轨迹是否一致。需要逐请求token和HTTP错误／退避细节时，应使用自己可观测的模型API执行链路，但那不自动消耗Cursor订阅池。
