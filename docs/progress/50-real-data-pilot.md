# 第 50 轮：DeepSeek 真实执行与后端数据采集

日期：2026-11-05。用户授权模型 deepseek-flash、累计预算 60 元。轨迹与用量优先，批次原始数据只留在被忽略的 runs/real-20261105/。

## 已完成

- 盘点 12 条历史尝试：确认三个自建终端任务真实 DeepSeek 成功记录，以及此前 fix-git 的预算停止和成功记录，直接复用。
- Docker Desktop 启动并确认服务可用；按项目锁文件安装隔离 Harbor 0.25.0 于 runs/harbor-venv，SWE-bench 可选依赖 5.0.2 安装到项目环境。没有修改依赖声明或锁文件。
- DeepSeek 小请求验证通过，代理保存原始请求／响应及缓存、推理用量。密钥经隐藏输入，仅留进程内存，不在命令参数、文件与日志中保存。
- Native fix-git 完成 11 步模型轨迹。模型执行了合并，但评分脚本因 apt/curl/uvx 依赖下载问题未执行测试却写 reward=0，原始 wrong 不能直接解释为模型失败；原始证据保留，索引标 verifier_bootstrap_failed。
- Harbor＋Terminus-2 真实 fix-git reward=1，12 回合，输入 58,778、输出 7,263、缓存 49,152 token；cost_usd 未报告。结束确认没有本 Job 残留容器。
- Native regex-log 在镜像拉取阶段 env_error，没有模型调用。
- SWE pytest-dev__pytest-5227 在镜像阶段 env_error；已固定 HF 数据版本。后续明确官方镜像只有 amd64，与宿主 arm64 不同，准备显式架构镜像。
- JobBench 官方源码固定 660b33a86ed032559df0b2fdc1ad38cf7ceb83f3，HF 数据固定 d43a66575635da0d3f6cb9a20976dc1a60f3ffc9。官方路线固定 Harbor 0.22.0，未冒充与本项目兼容。
- JobBench computer_user_support_specialists/task1 做两次无外网 Native 生成试跑。80k 预算首次无交付；240k 第二次增加增量落盘提示，保存 6 个中间分析文件，仍为 token_limit，未完成最终 DOCX／CSV／SQLite 交付。官方 Judge 未执行，scorer_error 与生成结束原因均保留。由于同时改变提示和预算，不属于严格单变量对照。

## 原始证据与索引

本地 report.html 提供真实结果与文件入口；evidence/ 保留 API 请求响应，ledger.jsonl 保存逐调用用量。试验 SQLite 索引已幂等构建两次，6 条尝试、22 个产物、57 次 API 请求，产物 SHA-256 和外键校验通过。累计输入 462,281、输出 20,412、缓存输入 408,064、推理输出 10,305 token。子项不可重复相加。

按高峰价、全部输入未命中缓存、8.5 元／美元保守计提约 1.387 元，不是实际账单；供应商费用为准。用户随后核对账户，表示实际显示约0.2元且并非全部属于今晚；无法据此分摊本批实际费用，本批实际账单金额保持未知。预算代理按已消费加请求预留检查 50 元门槛，给 60 元授权保留余量。

数据契约初稿见 [真实运行数据契约](../real-run-data-contract.md)。重要缺口为稳定的请求与任务尝试关联 ID、独立的生成／评分状态，以及 Native 缓存／推理字段的统一读取。未创建正式数据库迁移或后端 API。

## 限制与下一步

JobBench 有网络试跑被自动审批拒绝，理由为模型命令可能外传本地任务材料；改用无网络容器执行，外部参考限制如实记录。无官方评分，不能发布为完整 JobBench 成绩。

SWE amd64 镜像显式下载在600秒到限后停止，镜像检查确认未完成，SWE没有调用模型。预算代理已停止；Docker检查未发现本轮 Native 残留容器，Harbor运行结束检查也确认无残留。归档173个证据文件的哈希清单，文本归档扫描未发现API key格式内容。后续先修补数据关联与评分有效性，再接只读页面，不依据小样本声称通用能力提升。IDE 自动出现的 .idea/jobbench*.iml 文件未修改、提交或清理。
