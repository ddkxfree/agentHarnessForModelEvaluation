# 从模型问答到 Agent 任务：逐步运行与理解

日期：2026-10-28。目标是能解释一次完整评测，再比较模型和 Agent；不是立即全量跑榜。第一轮用户选择环境与 Oracle 检查，不调用真实模型。API 服务商、模型和调用预算待后续确定。

实际进展：已配置 Docker Desktop 使用本机 7897 代理，Python 官方镜像可运行；三个自建任务 Oracle 3/3、不操作全失败，Docker 正反例测试通过。证据见 [35](progress/35-docker-proxy-and-oracle.md)。下一步进入模型小样本调用，尚未执行。

## 先区分五个对象

| 对象 | 做什么 | 本项目对应 |
|---|---|---|
| Benchmark／数据集 | 提供题目、材料、初始环境和评分规范 | Task 插件 |
| 模型 | 根据消息生成答案或动作 | Model／generate；本地服务或独立 API |
| Agent | 组织提示词、工具调用、观察反馈和停止条件 | Solver；BasicAgent、mini-swe-agent |
| 执行环境 | 真正运行命令、改文件、保留任务状态 | 每题 Docker 沙箱 |
| 评分器 | 检查答案、补丁、文件或 rubric | Scorer；有些评测还需独立裁判模型 |

模型 API 返回工具调用并不等于命令已经执行。Agent 读取调用，平台在容器执行，再把结果返回模型，直到提交或预算耗尽。SWE／终端题评到的是模型、Agent、工具、预算和环境共同形成的系统。

现有 Model 主要兼容 OpenAI Chat Completions 格式。配置 key 之外还需 endpoint、模型名和所需能力；原生不同协议需要适配。BasicAgent 使用结构化工具调用，mini 使用自己的动作格式，因此模型能回答聊天不代表能兼容所有 Solver。现有 API key 不保证有额度或有权访问指定模型；Cursor 开发额度不是 Harness 的 API 预算。

## 三个感兴趣的评测集

| 评测集 | 任务／评分 | 项目现状与安排 |
|---|---|---|
| SWE-bench | 根据真实仓库问题生成补丁，在测试环境验证修复 | 已接 SWE-bench Lite，先单题 Oracle，再 Solver，再 verify |
| Terminal-Bench | 在终端环境完成任务，用验证器检查最终状态 | 已固定 TB 2.0 的 5 题；先自建 3 题，再真实 TB，不将小子集结果称作官网全榜成绩 |
| JobBench | 多源职业材料与交付物，依据加权 rubric 由模型裁判评估 | 尚无 Task 插件；先用官方流程跑一题，再规划适配、产物提取和官方评分复用 |

这里按用户所说 Job-bench 指 JobBench 官方项目；若实际是另一个站点，以用户链接为准。JobBench 需把执行模型／CLI 认证与评分裁判配置分开。当前通用 Judge 不等于已支持它的交付物提取和官方聚合规则。

核查来源（2026-10-28）：[SWE-bench 评分](https://www.swebench.com/SWE-bench/guides/evaluation/)、[Terminal-Bench](https://www.tbench.ai/)、[JobBench 官方评测仓库](https://github.com/Job-Bench/job-bench-eval)。固定本项目数据版本，不因官网版本更新自动替换现有任务。

## 学习与执行顺序

每一步保留运行目录、配置、实际调用数、用量、耗时、停止原因和未完成项。先并发 1、单次运行；配置中的 token_limit 不是严格费用上限，最后一次调用可超出剩余 token 预算。

| 步骤 | 实际操作 | 要能解释的问题 | 完成证据 |
|---|---|---|---|
| 0 环境 | 确认依赖、Docker 服务、arm64／镜像架构；不调用模型 | Docker CLI 与 daemon 有什么区别？ | Docker 连通、缺依赖清单 |
| 1 Oracle | 三个自建终端任务执行参考解；再跑已有 no-op 集成测试 | 如何证明题目、环境和判分器可用？ | Oracle 3/3；no-op 都失败；无遗留容器 |
| 2 一次模型问答 | API 或本地服务跑 1～3 条 GSM8K | prompt、回答、抽取和评分有什么区别？ | 完整 manifest、原始输出、评分 |
| 3 一次 Agent | BasicAgent 跑 count-errors 一题 | 谁决定动作？谁执行 bash？工具结果如何回到模型？ | 可逐步解释的工具轨迹 |
| 4 比较 | 先固定 Agent 换模型，再固定模型换 Basic／mini | 差异来自模型还是 Agent？ | 同题、同预算政策的结果与失败分析 |
| 5 真实 TB | 固定的一两道任务先 Oracle 后 Agent，再扩大到现有 5 题 | 自建题与官方任务有何区别？ | 验证脚本、任务版本、轨迹、结果 |
| 6 真实 SWE | 单题参考补丁、Agent 修复、全新容器 verify | 补丁生成与判分为何分开？ | patch、测试日志、独立复核 |
| 7 JobBench | 阅读一题材料、交付物与 rubric，官方路径先跑一题 | 单测与 rubric 判分有何差异？ | 工作产物、逐条判分、裁判原文及费用 |
| 8 小实验 | 做一次真实单变量消融；小批人工标签校准 Judge | 为什么一个分数不能解释成因？ | 配对结果、局限、校准覆盖率 |
| 9 接入练习 | 根据 JobBench 官方试跑暴露的接口需求设计 Task／Solver／scorer | 接新题库与接新 Agent 各改哪层？ | 固定数据版本、正反例、原生与适配结果核对 |

步骤 7／9 是新工作，尚未实现；不会假装可以直接运行 `harness run -t jobbench`。阶段八接入 Agent 可辅助生成候选，但仍需独立验证和人工评审，不能自动替代官方任务协议。

## 第一轮命令与验收

在当前项目目录执行。已有 .venv 时可以用 `.venv/bin/harness` 代替 `uv run harness`；uv 缓存不可写时不必重新安装依赖。

```bash
docker info
uv run harness list-tasks
uv run harness run -m none/none -t terminal -p terminal.solver=oracle --concurrency 1
uv run pytest -q tests/test_docker_sandbox.py -k 'oracle_solves_every_builtin_terminal_task or untouched_environment_fails_every_builtin_terminal_task' -rs
```

最后一条选中两个测试，分别覆盖三个自建任务的 Oracle 与不操作对照；skipped 不算通过。当前 CLI 的 terminal.solver 只有 agent／mini／oracle，没有 noop 选项；不操作对照通过现有集成测试完成。首次构建／拉取镜像需要网络、磁盘与时间，但不产生模型调用。

待 Docker 可用后的测试执行以实际选中用例为准，不能仅因命令退出码 0 认定 Oracle 已过。

## 模型与 Agent 如何接入

- 换已有兼容模型：配置 provider/model、端点与本机凭据，Task 和评分规则保持不变；新协议才需要增加 Model 适配。
- 换现有 Agent：配置 solver=agent／mini。mini 的可选依赖需安装，且动作格式能力要验证。
- 接新 Agent：新增 Solver 适配，接收 Sample、generate 和 sandbox，返回 SolveResult 与轨迹，describe 记录协议／版本。外部 CLI 或服务还需桥接工作目录、生命周期、产物和认证；不是把其名字填进 model 就能测。
- 接新评测集：新增 Task，负责加载数据、构造环境与评分；复用已有模型和 Solver，原则上不改 core。

使用 API 模型时推理在服务端，本机仍执行容器任务。是否需要 GPU 取决于是否本地部署模型，不是 Agent 评测本身必须配 GPU。
