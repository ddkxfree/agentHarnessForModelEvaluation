# 执行证据与用量记录（schema 2）

本轮先补记录层，再用已有环境验收。没有升级 Harbor／TB／SWE Pro，也没有接网页或建立正式业务数据库。

## Native 记录链路

`run_eval` 分配 run_id；每个样本每次执行分配独立 attempt_id。一次 Solver 的 generate 是一个 logical_call_id，每次真实重试分配不同 call_id；Step 引用真实 call_id。工具执行以 call_id＋工具序号生成 tool_execution_id，另存供应商 tool_call_id。缓存回放生成新 ID，origin_call_id 指向原始请求；它的 token 不计入本次 fresh_usage。

每个 attempt 保存两个文件：

```text
<run>/<task>/artifacts/<sample>/evidence/<attempt_id>/events.jsonl
<run>/<task>/artifacts/<sample>/evidence/<attempt_id>/checkpoint.json
```

事件有 schema_version、run_id、attempt_id、sample_id、epoch、递增 sequence 与 UTC 时间。包括排队、生成开始／结束、模型请求开始／完成／异常、缓存回放、步骤、工具开始／结束／中断、产物、评分开始／结束和 attempt 结束。原始成功响应保留供应商公开返回的完整 JSON，包含适配器未抽取的字段。请求保存 messages、tools、配置和模型身份，不保存认证头。Native Judge 同样记录逐次请求，费用与被测模型分开。

事件每次 append 后 flush＋fsync；快照临时文件 fsync 后原子替换；最终 samples.jsonl 重排也采用原子替换。已完成样本持续追加并 fsync。取消时会记录执行中与仍在排队的 attempt；当前步骤、已返回响应和产物可在快照／事件里追溯，不能仅依赖最终 samples.jsonl。文件系统／硬件故障仍可能造成写入失败；突然强杀时，以已经落盘的事件为准，started 而没有结束的请求保持未知，不能推断没有费用。

生成和评分单独记录：generation_status／generation_finish_reason、assessment_status。例如本轮 openssl 题生成因 token_limit 停止，评分仍 completed 且 reward=1；不能因为预算停止就抹去分数，也不能用分数掩盖预算停止。`completed` 表示 Solver 已返回；供应商输出截断仍由 finish_reason=length 单独呈现。环境失败继续归 env_error。

## 用量口径

Usage 增加 cached_input_tokens、reasoning_output_tokens、两个包含关系标识和 source。DeepSeek 的缓存 token 属于输入 token 子集，推理 token 属于输出 token 子集，不能四项相加。所有记录保留原始 provider usage 可复核。

- 已报告 0 与未报告 null 不同；老文件保持默认兼容，record_schema_version=1，阶段状态为 unknown，不追补虚构 ID。
- 汇总主字段遇到未报告值保持 null。失败／取消的请求用量未知，coverage 明确统计；成功响应的已知 token 小计不宣称覆盖未知请求费用。
- fresh_usage 只计本次实际返回的响应；缓存回放另计。重试失败也有独立记录，其未知费用不会被当成明确的零。
- 带 token_limit 的样本如果供应商没有给完整主用量，下一次调用前停止并报告不能执行预算检查。token_limit 在调用前检查，单次响应可以越过阈值；它不是供应商侧的严格总 token 截断。
- 持久化结构中的常见凭据字段、环境中 KEY／TOKEN／SECRET／PASSWORD 的值和常见 sk- 格式会脱敏；不改变真实发送给模型的输入。该处理不等于任意业务隐私都可自动识别，原始归档默认留本地。

## Harbor 0.25.0 的边界

导入器版本升到 3、映射版本升到 2。run_id 与 attempt_id 基于已有 Job／Trial 身份确定性生成，重复导入保持一致。旧归档通过 `harness harbor rebuild <run_dir>` 更新，不能将缺失的历史逐步用量补成 0。

本机 Harbor 的内部异常重试会删除原 Trial，故配置现要求 max_retries=0；重跑可另建 Job。显式 resume 在启动 Harbor 前，先把旧 Job 复制到私有 `local/attempt-history/resume-<timestamp>/`，归档失败则拒绝恢复，保留符号链接而不跟随它复制外部文件。原归档路径记录在运行历史的 previous_evidence。

这保证恢复前文件有副本，不能保证外部 CLI 未公开的内部重试、隐藏推理或逐步 token 都能捕获。Harbor 的生成／评分状态仅从已存在阶段证据映射；未知保持 unknown。Native 请求级完整记录与第三方智能体可见记录需要在未来页面中分别标出覆盖范围。

## 本轮真实验收

`runs/observability-20261105/`（Git 忽略）保存 5 题并发 2 的 Native BasicAgent＋DeepSeek Flash 试跑：

| 题目 | 评分 | 生成停止原因 |
|---|---:|---|
| fix-git | 1 | submit |
| log-summary-date-ranges | 1 | submit |
| openssl-selfsigned-cert | 1 | token_limit |
| regex-log | 1 | length |
| sqlite-db-truncate | 0 | length |

34 次请求，输入 121,644、输出 27,564、缓存输入 103,552、推理输出 20,830 token。代理原始 usage 与 Native 逐请求记录完全匹配，34 个 Step、34 个工具执行、13 份产物、213 个事件均可关联；工具执行数与 Step 数相同只是本批合计巧合，一步可以多个工具或没有工具。

`audit.py` 校验身份、请求用量、快照与最终结果一致、工具配对、产物大小和 SHA256，并将冻结结果两次导入 `acceptance.sqlite`：始终 5 attempts／34 calls／213 events，没有重复计数。该 SQLite 是验收样例，不是正式数据库迁移。`report.md` 与 `audit.json` 保存结论，`batch-config.json` 保存预算和环境条件。

本批保守估算 0.591345 元，实际供应商扣费未知。它不是账户账单；此前用户反映账户约0.2元属于先前时点。预算代理已停止，没有残留本批评测容器。成绩只代表5个选择题、Native BasicAgent和预装判分依赖的工程样本，不作为完整或官方 Harbor Benchmark 成绩。

下一步按样本设计持久化表和只读 API，再接网页轨迹页；新版本环境分别建环境、适配解析器和回归样例后接入。
