# 路线图与设计背景

## 项目目标

这是一个用来学习和展示"大模型评测平台"工程能力的项目，对标的是"大模型评测研发工程师"岗位的要求：

- 评测平台架构：数据集管理、环境隔离、任务调度、指标计算、结果归档与可视化
- 评测集适配：SWE-bench、Terminal-Bench 等主流评测集的平台化接入，Benchmark 版本管理，结果可追踪、可对比、可复现
- Harness Engineering：把评测任务本身 Agent 化，自动接入评测集；自动诊断适配异常、指标抖动、环境漂移
- 前沿评测范式：LLM-as-Judge、多智能体评判、人类偏好对齐

核心定位：**我们做的是"考试中心"，不是"考生"。** mini-swe-agent、SWE-agent、OpenHands 这类 Agent 是被评测的对象，应当作为 `Solver` 插件接入；SWE-bench、Terminal-Bench 等评测集作为 `Task` 接入。不要重新实现这些 Agent。

## 设计原则

1. **生成和打分分离**。模型原始输出全部落盘到 `samples.jsonl`，改抽取或打分逻辑后用 `harness rescore` 离线重打分。
2. **Solver 拿到的是包装好的 `generate`**（已带缓存、重试、超时）。多步 Agent 循环直接多次调用它即可，不需要关心这些工程细节。
3. **可复现**：数据集固定 revision；solver/scorer 通过 `describe()` 产生指纹写入 manifest；manifest 还记录 git commit、依赖版本、`uv.lock` 哈希。
4. **诊断优先**：每道题归为 `correct / wrong / extract_failed / truncated / api_error`，并记录抽取方式；报告 bootstrap 置信区间。
5. **加新评测集不改 core**。如果做不到，说明抽象需要调整，先调整抽象。

## 当前状态

每一轮的详细记录见 [`docs/progress/`](progress/README.md)。

2026-10-25 阶段六复评基准为 master `881036b`（PR #19）。主体链路和现有 Judge 测试通过，本机 272 passed、18 skipped，静态检查通过；纯后处理变更被误判需新裁判调用、校准输入校验两项待补修，见 [22](progress/22-stage6-review.md)。真实裁判校准仍未验证。下列较早的提交与测试数字为历史记录。

理解后续阶段可先读 [后续阶段说明](next-stages.md)。阶段十历史评审见 [17](progress/17-stage10-review.md)。当前已更新到 master `21e9665`，阶段十源码和补修均已合入；本次 225 passed、18 skipped，静态检查通过。T1 并发预算与 T3 可选依赖测试通过复核；T2 尚漏跨条件的任务内额外差异，见 [19](progress/19-stage10-fix-review.md)。真实消融仍待运行。T2 补修见 [20](progress/20-stage10-t2-fields.md)，已合入 master（PR #18）。[阶段六最小 Judge](stage6-plan.md) 已实现，见 [21](progress/21-judge-minimal.md)：最小实现已完成、真实校准待验证。

历史复评记录：第 11 轮评审时工作区停在 PR #6，从缓存的 PR #10 分支 `c950fa5` 导出审查了累积源码，本机 179 passed、14 skipped；详见 [11](progress/11-stage5-review-and-next.md)。当前已更新到 `8ebc843`，包含阶段七。阶段七复评本机 198 passed、16 skipped，静态检查通过；消融条件间缓存隔离、恢复后的耗时口径和部分分数一致性仍需补修，见 [14](progress/14-stage7-review.md)。真实 TB／SWE Solver 对照仍待运行。

- 阶段 1～4 已完成：GSM8K、MMLU；并发、重试、缓存续跑；manifest、rescore、diff；诊断统计。见 [01](progress/01-platform-core.md)。
- 阶段 5 第 1～5 步已完成：Docker 沙箱、`env_error`、基线工具调用 Agent、轨迹记录、3 个自建终端任务和 Oracle 校验。见 [02](progress/02-agent-sandbox.md)。
- 阶段 5 第 6、7 步已完成：SWE-bench Lite（官方镜像与判分逻辑、参考补丁 Oracle）、mini-swe-agent 作为 Solver 插件。见 [03](progress/03-swebench-mini-swe-agent.md)。
- 阶段 5 加固第一轮实现已提交：失败分类、分数校验、数字比较、提交校验与 CI 修正。见 [04](progress/04-correctness-diagnostics.md)。2026-10-24 复评暂未通过：终端 NaN 漏判未修、长小数仍舍入、重评遗留错误状态，见 [05](progress/05-review-correctness-diagnostics.md)。本机 96 passed、8 skipped，静态检查通过；Docker 与可选依赖集成测试未复核。
- R1～R3 已修复，待复评：见 [06](progress/06-review-fixes.md)。
- 加固第二轮（失败可追踪）已实现，待复评：见 [07](progress/07-traceability.md)。
- 加固第三轮（独立复核）已实现，待复评：见 [08](progress/08-independent-verification.md)。
- 加固第四轮（真实 Terminal-Bench 2.0，5 题）已实现，待复评：见 [09](progress/09-terminal-bench.md)。
- 加固第五轮（Solver 对照，`harness compare`）已实现并在自建终端任务上实测，待复评：见 [10](progress/10-solver-comparison.md)。
- 11 的 N1～N4 已补修，待复评：见 [12](progress/12-stage5-followup-fixes.md)。PR #7～#10 原先只合并进叠加分支，由 PR #11 合入 master。
- 阶段 7 最小版（重复实验、恢复、配对统计、失败报告）已实现，MockModel 测试通过，真实多 epoch 运行待验证：见 [13](progress/13-statistics-diagnostics.md)。复评发现 S1～S3，须补修后验收：见 [14](progress/14-stage7-review.md)。S1～S3 已补修，待复评：见 [15](progress/15-stage7-review-fixes.md)。
- 阶段 10 最小版（token 预算、Solver 协议记录、预算敏感性／机制消融分类）已实现，MockModel 测试通过，待复评；真实单变量消融（观察截断 4000 对 500）配置已备好，尚未运行，待本地或外部 API 验证：见 [16](progress/16-budget-ablation.md)。复评发现 T1～T3（[17](progress/17-stage10-review.md)），已补修，待复评：见 [18](progress/18-stage10-review-fixes.md)。阶段十源码原先只合并进阶段七补修分支（PR #16），随本轮补修一起合入 master。19 复评后 T2 残留（任务条目内额外差异）已补修，待复评：见 [20](progress/20-stage10-t2-fields.md)。
- 阶段 6 最小版（rubric 单答案评分、与参考答案的 A/B 双序比较、双裁判聚合并保留分歧、裁判输入／原文／用量／版本完整归档、离线重解析、人工标签一致率入口）已实现，MockModel 测试通过，待复评；**最小实现已完成、真实校准待验证**，配置见 `configs/judge-open-qa.yaml`：见 [21](progress/21-judge-minimal.md)。复评发现 J1、J2（[22](progress/22-stage6-review.md)），已补修，待复评：见 [24](progress/24-stage6-review-fixes.md)。
- 326 个测试（只装基础依赖时 301 passed、8 skipped；Docker 集成测试在无 Docker 或无镜像时自动跳过），GitHub Actions CI，master 上已确认触发。

### 阶段 5 加固计划（先把结果做可信，再扩任务数量）

1. 正确性与诊断：非法提交、失败分类、分数校验与 CI 已修；终端 NaN 漏判、长小数精度与重评状态恢复已补修（[06](progress/06-review-fixes.md)），待复评。
2. 失败可追踪：中途异常保留轨迹、完成样本及时落盘、清理失败要记录、单题总时长限制。已实现（[07](progress/07-traceability.md)），待复评。
3. 独立复核：完整产物、环境身份（镜像 digest、服务端模型版本）、新容器评分、SWE-bench 不调模型重评。已实现（[08](progress/08-independent-verification.md)），待复评。
4. 接入真实 Terminal-Bench（固定版本，3～5 题；Oracle 全过、不操作全失败；不改 core）。已实现（[09](progress/09-terminal-bench.md)），待复评。
5. 有意义的 Solver 对照：固定模型、样本、预算和缓存策略，记录成功率、token、耗时和失败归类。已实现（[10](progress/10-solver-comparison.md)），待复评；Terminal-Bench／SWE-bench 上的对照尚未运行。

缓存复用策略已在第 3 轮明确（`cache_policy: auto`，temperature > 0 时默认不复用），完整的多轮次 epochs 留到阶段 7。

十阶段初步完成的最小验收、给 Opus 的开发范围和最终试运行清单见 [05 复评记录](progress/05-review-correctness-diagnostics.md)。建议顺序为当前补修 → 阶段 5 加固／阶段 9 最小适配 → 阶段 7 → 阶段 10 → 阶段 6 → 阶段 8；仍按 Opus 开发、Codex 逐轮评审推进。

## 下一步

### 阶段 5：Agent 评测与 Docker 沙箱

目标：平台能评测"模型 + Agent 框架"在需要执行环境的任务上的表现。

1. ~~`Sandbox` 抽象和基于 Python `docker` SDK 的实现，每个样本一个容器，结束必须清理。~~ 已完成：`sandbox/`
2. ~~Runner 按样本创建和销毁沙箱，传给 solver 和 scorer；环境故障归为 `env_error`。~~ 已完成
3. ~~基线工具调用 Agent（`bash` + `submit`，步数上限）。~~ 已完成：`solvers/basic_agent.py`
4. ~~轨迹记录写入 `samples.jsonl`，`harness show --sample` 查看。~~ 已完成
5. ~~自建 3 个 Terminal-Bench 风格的小任务，以及 Oracle Solver。~~ 已完成：`tasks/terminal_data/`
6. ~~接入 SWE-bench Lite / Verified 的少量样本：复用官方 Docker 镜像和单测验证逻辑，包装为 Task 和 `SandboxScorer`。~~ 已完成：`tasks/swebench.py`
7. ~~把 mini-swe-agent 包装为 Solver 插件，和基线 Solver 在同一批题上对比。~~ 已完成：`solvers/mini_swe_agent.py`（本地 3B 模型两者都是 0/2，需要用更强的模型对比）
8. ~~接入真实的 Terminal-Bench 任务（或通过 Harbor 的任务格式），验证不改 core 就能容纳第二种 Agent 评测集。~~ 已完成：`tasks/terminal_bench.py`，Terminal-Bench 2.0 的 5 道题

验收：同一个模型分别配基线 Solver 和 mini-swe-agent，在自建任务和 SWE-bench 小样本上都能跑出结果；`harness show` 能看到完整轨迹；沙箱不泄漏容器。

### 阶段 6：LLM-as-Judge

下一轮最小交付、架构边界、MockModel 测试和真实验证范围见 [阶段六计划](stage6-plan.md)。

1. ~~`JudgeScorer`：单点打分（rubric）和两两对比两种模式；裁判模型通过同样的 `get_model` 配置。~~ 已实现（[21](progress/21-judge-minimal.md)）：`judge/`、`open-qa` 任务；两两对比目前只与参考答案比
2. ~~两两对比时交换 A/B 顺序各评一次，不一致记为 unstable／inconclusive，与明确平局分开，并统计顺序不一致率。~~ 已实现：`inconsistent` 与 `tie` 分开记录，`summary.judge.labels` 统计
3. 多裁判投票。最小版已实现：两个裁判，全部有效时取平均，保留各自判断与分歧；多数投票等规则未做
4. 接入 MT-Bench 或 Arena-Hard 的少量样本作为验证。未做
5. 用一小批人工标注（或 lmarena 公开的人类偏好数据）计算裁判与人类的一致率。入口已实现：`harness judge-agreement`；真实标签与真实裁判运行待验证

注意：裁判调用也要走缓存和 manifest，裁判模型和裁判 prompt 的变化必须体现在 scorer 指纹里。

### 阶段 7：统计与诊断

1. ~~同一配置跑 N 次（`--epochs`），缓存键加入 epoch，报告均值、标准差和置信区间。~~ 已实现（[13](progress/13-statistics-diagnostics.md)），真实运行待验证
2. ~~两个模型的配对比较与显著性检验（同一批题）。~~ 已实现：`diff` 与 `compare` 的配对差、bootstrap 区间、符号检验
3. ~~`harness diff` 增加"可能原因"提示：配置变化、环境变化、输出完全相同但打分变化（说明是 scorer 改动）等。~~ 已实现：`likely_causes`
4. ~~错误归类报告：自动抽样各类失败样本，生成 Markdown 报告。~~ 已实现：`harness report`

### 阶段 10：Harness 对照与消融

1. ~~单样本 token 预算，超出后保留已有工作并评分，outcome 记为 `token_limit`。~~ 已实现（[16](progress/16-budget-ablation.md)）
2. ~~Solver 协议与能力记录（接口、工具、结束方式、步定义、依赖）写入 manifest。~~ 已实现：`solver_protocol`
3. ~~compare 报告各条件预算，区分 Solver 对照、预算敏感性和机制消融。~~ 已实现：`budgets`、`equal_budget`、`experiment_type`
4. 一次真实单变量机制消融（固定模型、样本、种子和预算，每条件 3 次）。配置已备好：`configs/ablation-observation-terminal-bench.yaml`，待本地或外部 API 运行

### 阶段 8：评测集接入 Agent 化

可直接交给 Opus 的第一版范围、执行循环、独立验证与回归验收见 [阶段八开发指引](stage8-plan.md)。当前为计划，尚未实现；阶段六 J1／J2 补修与真实验证状态继续单列。

用一个 Agent 自动接入新评测集：读取数据集说明和样例，按 `tasks/` 的约定生成 Task 文件，用已知模型试跑并与论文或排行榜分数对比，分数对不上时查看失败样本、修改并重跑。

### 之后

- 分布式执行（Ray 或 K8s Job），结果存储换成数据库，简单的 Web 结果页
- 多模态评测（参考 VLMEvalKit、lmms-eval）
- 工具调用与多轮对话评测（BFCL、τ²-bench）
