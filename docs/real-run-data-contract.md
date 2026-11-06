# 真实运行数据契约初稿

后续更新：Native schema2与5题并发验收已补齐稳定关联，见[执行证据说明](observability.md)。下文是首轮试跑的历史问题与后端API草案，API仍未实现。

日期：2026-11-05。依据：本机 Native、Harbor 0.25.0 与离线 JobBench 生成试跑。不是已实现的后端 API。

## 原始证据与索引

本轮本地根目录为 `runs/real-20261105/`，被 Git 忽略。`native/`、`harbor/`、`jobbench/` 保留各自运行产物，`evidence/` 保留代理看到的完整请求和响应（不含认证头），`ledger.jsonl` 保留原始用量及保守费用估算。`build_index.py` 可重复构建 `evidence.sqlite`，验证产物哈希和外键。

索引用于验证结构，不作为正式数据库迁移。API 请求目前缺少稳定的 attempt 关联标识，试验索引对应外键保持 NULL，不按时间猜测归属。正式执行应在启动时分配 run/attempt/call 标识，并由适配层传递。

## 建议实体

| 实体 | 关键字段与关系 |
|---|---|
| experiments | id、执行来源、完整配置、任务版本、模型请求名、Agent 版本／指纹、运行状态 |
| attempts | experiment_id、task_id、task_fingerprint、repeat_index、retry_index、独立的 generation_status／finish_reason 和 assessment_status |
| messages / tool_calls | attempt_id、顺序、角色、调用 ID、参数、结果关联、原始记录引用 |
| model_calls | attempt_id、request_id、请求／返回模型身份、延迟、finish_reason、请求／响应引用 |
| usage_records | model_call_id 或 attempt_id、粒度、输入／输出／缓存／推理 token、缓存包含关系、已报告／未知／不适用 |
| assessments | attempt_id、评分器版本、评分状态、原始 reward、有效性、证据、重评历史 |
| artifacts | attempt_id、文件名、类型、大小、哈希、存储引用、产物用途 |
| cost_estimates | 用量引用、价格版本、币种、汇率假设、时段、估算方法和结果 |

数据库保存可查询元数据；大日志、补丁和文件继续放独立文件归档。统一视图可以重建，原始记录保留不覆盖。

## 已发现的实际差异

1. Native BasicAgent 保存逐步输入／输出 token，但没有缓存和推理 token 的统一字段。DeepSeek 原始 API 响应提供这些值，代理已保留。新增字段时必须保留来源与缺失语义。
2. Harbor 的 agent_result 提供 n_input_tokens、n_cache_tokens、n_output_tokens 及 ATIF 轨迹，本次 cost_usd 为 null。不能用 null 当成零费用。
3. Native fix-git 评分器依赖安装失败，uvx 未执行，但官方脚本仍写 reward=0，当前平台归为 wrong。需要保留 raw_reward，同时将 assessment_validity 标为 bootstrap_failed，不能直接算模型答错；后续应基于明确的验证器就绪证据修复分类。
4. JobBench 生成因 token_limit 停止，官方 Judge 没有配置；当前结果记 scorer_error，输出 finish_reason 仍保留 token_limit。数据库必须分别记录生成停止和评分状态。
5. JobBench 可产生中间分析文件而没有最终 DOCX／CSV／SQLite 交付物。artifact 存在不等于任务完成，空归档也不等于有交付文件。
6. 两轮 JobBench 同时改变预算和“增量落盘”提示，不能视为严格的单变量预算对照。应保存全部输入与协议差异。
7. SWE 官方 pytest 镜像只有 amd64，本机是 arm64；镜像平台与宿主平台需要记录。镜像准备失败不是模型失败。

## 只读 API 第一批

- GET /api/experiments：摘要，原始来源、状态、评分覆盖率和缺失提示。
- GET /api/experiments/{id}：配置、尝试、结果与报告链接。
- GET /api/attempts/{id}/trajectory：顺序消息、工具调用与反馈，长内容以文件引用读取。
- GET /api/attempts/{id}/assessments：评分历史和有效性，不与生成状态混为一项。
- GET /api/artifacts/{id}：经权限与路径检查的下载或文本预览。
- GET /api/comparisons?a=...&b=...：可比性、配对样本及排除原因。

先接真实归档只读展示，网页启动任务与实时事件流后续再做。当前没有创建这些 API。

## 费用与使用限制

代理只允许 deepseek-flash，每次输出上限 8192 token；按输入全部未命中缓存、高峰美元价格和 8.5 元／美元的保守假设计提，接近 50 元拒绝新请求，给 60 元总额度保留余量。无用量响应按请求预留保守记账，重启从账本恢复累计。这是本地估算，不能替代供应商实际账单。

所有评测仅作工程样例，不能发布成完整 Benchmark 成绩。JobBench 本次为无外网、标准库环境的 Native 修改版生成试跑，非官方完整评测；缺少官方 Judge。原始任务代码与数据均固定了版本。

费用纠正：用户账户显示约0.2元，且不是全部在本次晚上产生。1.387元为本地保守计提，不是实际扣费；本批实际费用需按供应商请求级账单核对，当前保持未知。
