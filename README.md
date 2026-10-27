# agentHarnessForModelEvaluation

一个用 Python 实现的大模型与 Agent 评测 Harness：接入题目、驱动模型或 Agent、隔离执行、评分，并保存可复核的轨迹和结果。平台是“考试中心”，模型与 Agent 是被评对象。

目前具备的实现（验收状态见下文）：

- **平台内核**：用 GSM8K 和 MMLU 跑通"加载数据 → 构造 prompt → 调模型 → 抽取答案 → 打分 → 统计 → 归档"的完整链路，具备并发、重试、缓存续跑、可复现记录、离线重打分和运行对比能力。
- **Agent 评测**：每道题一个 Docker 容器，Agent 通过工具调用在容器里执行命令，结束后由测试脚本检查容器状态来判分；完整记录 Agent 每一步的轨迹。
- **SWE-bench 与 mini-swe-agent**：接入 SWE-bench Lite（官方镜像和判分逻辑，补丁在全新容器中判分，可用 `harness verify` 独立复核）；mini-swe-agent 作为可替换的 Solver 插件，和基线 Agent 在同一批题上对比。
- **真实 Terminal-Bench**：固定版本的 5 道 Terminal-Bench 2.0 任务，复用 Harbor 任务格式和验证脚本，镜像固定 digest。
- **重复实验与消融**：epochs、条件独立缓存、恢复、配对统计、失败报告，以及 token 预算和机制／预算实验分类。
- **模型裁判**：rubric 与 A/B 双序比较、多裁判分歧记录、裁判调用独立归档、离线重解析和人工标签一致率入口。
- **自动接入工作流**：Agent 规划、生成候选 adapter、独立检查、接收诊断并修复，导出待人工评审的 diff。

功能实现、模拟测试和真实实验分别验收。阶段六 J1／J2 补修已通过本轮回归；阶段八评审发现的执行隔离、字段映射验收和总时限问题（[27](docs/progress/27-onboarding-review.md)）已补修，待复评，见 [28](docs/progress/28-onboarding-review-fixes.md)。真实 TB／SWE Solver 对照、消融、裁判校准和自动接入仍待验证。

阅读入口：[上手练习](docs/hands-on.md)、[进度索引](docs/progress/README.md)、[路线图](docs/roadmap.md)、[后续阶段解释](docs/next-stages.md)、[本地试跑与并行开发](docs/post-ten-plan.md)。上手文档中的旧仓库／分支命令仍需修订；在已有项目目录使用下面的命令。

## 快速开始

```bash
# 安装依赖（需要 uv：https://docs.astral.sh/uv/）
uv sync

# 问答类评测：本地 Ollama，不需要 API key
ollama pull qwen2.5:0.5b-instruct
uv run harness run -c configs/ollama-smoke.yaml

# 问答类评测：云端 API（以阿里云百炼为例）
export DASHSCOPE_API_KEY=sk-...
uv run harness run -m dashscope/qwen-plus -t gsm8k -t mmlu --limit 50

# Agent 评测（需要 Docker）
uv run harness run -m none/none -t terminal -p terminal.solver=oracle   # 先用参考答案验证任务本身
ollama pull qwen2.5:3b-instruct
uv run harness run -c configs/terminal-ollama.yaml
uv sync --extra swebench                                            # mini-swe-agent 是可选依赖
uv run harness run -c configs/terminal-ollama.yaml -p terminal.solver=mini   # 换成 mini-swe-agent

# SWE-bench（需要 Docker 和可选依赖）
uv sync --extra swebench
uv run harness run -m none/none -t swebench -p swebench.solver=oracle \
    -p 'swebench.ids=[django__django-11099, pytest-dev__pytest-5227]'     # 参考补丁，应全部解决
uv run harness run -c configs/swebench-ollama.yaml -p swebench.solver=mini
```

模型用 `provider/model` 指定，所有兼容 OpenAI 接口的服务都能接：

| provider | 默认 base_url | API key 环境变量 |
|---|---|---|
| `openai` | `OPENAI_BASE_URL` 或 OpenAI 官方 | `OPENAI_API_KEY` |
| `dashscope` | 阿里云百炼兼容模式 | `DASHSCOPE_API_KEY` |
| `deepseek` | `https://api.deepseek.com` | `DEEPSEEK_API_KEY` |
| `ollama` | `http://localhost:11434/v1` | 不需要 |
| `vllm` | `http://localhost:8000/v1` | 不需要 |

其他兼容服务可以用 `--base-url` 覆盖。`none/none` 是不调用模型时的占位，例如 Oracle 运行。

## 命令

```bash
harness list-tasks                                   # 列出已注册的评测集
harness run -c configs/example.yaml                  # 按配置文件运行
harness run -m ollama/qwen2.5:0.5b-instruct -t gsm8k --limit 20 \
    -p gsm8k.n_shot=3 --temperature 0 --seed 0       # 命令行参数覆盖配置
harness show runs/<run_id>                           # 汇总、抽取方式分布、Agent 统计
harness show runs/<run_id> -t gsm8k --outcome extract_failed -n 5   # 查看某类失败样本
harness show runs/<run_id> --sample terminal/count-errors           # 某道题的完整 Agent 轨迹
harness rescore runs/<run_id>                        # 改了打分逻辑后，不调模型直接重打分
harness verify runs/<run_id>                         # 在新容器里重新判分已保存的补丁（SWE-bench），不调模型
harness diff runs/<a> runs/<b>                       # 对比两次运行：配置/环境差异 + 逐样本翻转
harness sandbox-cleanup                              # 清理异常退出后残留的容器
harness run -c configs/ollama-smoke.yaml --epochs 3 --cache-namespace my-trial
harness compare -c configs/compare-terminal-ollama.yaml             # 条件对照
harness compare --resume runs/<compare_dir>                        # 请求级缓存恢复
harness compare-report runs/<compare_dir>                         # 离线重建对照报告
harness report runs/<run_id>                                      # 失败样本 Markdown 报告
harness run -c configs/judge-open-qa.yaml                          # 需要配置被评模型和裁判的 API key
harness judge-agreement runs/<run_id> --labels labels.jsonl        # 独立标签校准
harness rescore runs/<run_id> -p open-qa.disagreement_threshold=0.5 # 纯后处理离线重评
harness onboard -c configs/onboard-world-capitals.yaml -m <provider/model>   # 候选代码在容器中验证，需要 Docker
harness onboard-verify runs/onboard/<attempt>                      # 不调用模型，重验候选实现
```

`-p task.key=value` 的值按 YAML 解析，例如 `-p mmlu.subjects=[anatomy,virology]`。

`compare --resume` 会重新执行工具与评分，模型请求可从缓存回放。报告区分分数与性能的可比性。Judge 更换 rubric／实际提示词／裁判模型需要新调用，`rescore` 只重解析或重聚合已保存原文。onboarding 默认在无网络、只读根文件系统、只有临时验证目录可写的容器中执行候选代码，Docker 不可用时记为环境故障，不会退回宿主进程；`--executor subprocess` 没有文件系统隔离，只用于可信的脚本化候选。

## 架构

```text
src/harness/
├── core/
│   ├── types.py      Sample、ModelOutput、ToolCall、Step、SolveResult、Score、SampleResult、Outcome
│   ├── model.py      Model 抽象、兼容 OpenAI 接口的模型（含工具调用）、MockModel、provider 预设
│   ├── solver.py     Solver 协议和 PromptSolver（system + few-shot 多轮 + 问题）
│   ├── scorer.py     Scorer / SandboxScorer 协议，NumberMatchScorer、ChoiceScorer
│   ├── task.py       Task、DatasetSpec、任务注册表、确定性抽样
│   ├── runner.py     并发、重试、超时、缓存、沙箱生命周期、落盘、manifest
│   ├── cache.py      按请求内容哈希的磁盘缓存
│   ├── manifest.py   运行环境和任务指纹
│   └── config.py     YAML 运行配置
├── sandbox/          Sandbox 抽象和 Docker 实现
├── solvers/          basic_agent（bash + submit 工具调用循环）、mini_swe_agent（桥接 mini-swe-agent）、oracle
├── tasks/            gsm8k、mmlu、terminal、terminal_bench、swebench、open_qa
├── judge/            rubric、pairwise、解析聚合和校准
├── onboard/          接入规格、候选工作区、验证器、Agent 反馈修复和 diff
├── analysis/         stats、compare、rescore、verify、diff、report
└── cli.py
```

几个核心概念：

- **Task** = 数据集 + `Solver` + `Scorer` + 参数，需要执行环境的任务再加一个沙箱声明。
- **Solver** 拿到的是 runner 包装好的 `generate` 函数（已带缓存、重试、超时），Agent 循环可以多次调用模型；需要沙箱的任务还会拿到这道题的容器。
- **Scorer**：普通评分器读取原始输出；沙箱评分器检查任务环境；两阶段评分器收集提交物后在新容器验证；Judge 评分器通过独立裁判面板调用模型并归档。
- 新增评测集只需在 `tasks/` 下写一个文件并用 `@register("name")` 注册，不需要改 `core/`。

### 终端任务格式

仿照 Terminal-Bench / Harbor 的目录约定：

```text
tasks/terminal_data/<task-id>/
├── instruction.md          给 Agent 的任务描述
├── task.toml               元数据和测试超时
├── environment/Dockerfile  Agent 所在的容器
├── tests/test.sh           退出码 0 表示通过；Agent 结束后才复制进容器
└── solution/solve.sh       参考答案，供 Oracle Solver 使用
```

## 每次运行的产物

```text
runs/<run_id>/
├── manifest.json          运行配置、环境、每个任务的数据集版本和指纹、汇总结果
├── gsm8k/
│   ├── samples.jsonl      每道题一行：样本、完整 messages、原始输出、Agent 轨迹、token、耗时、打分、outcome、沙箱身份；每题完成即写入
│   ├── artifacts/         每题一个目录：被截断的工具调用的完整输出等（samples.jsonl 中记有哈希）
│   └── summary.json
└── ...
```

`manifest.json` 和逐样本归档记录了复核评测的主要信息：

- 模型 provider、名称、base_url（不含 API key）
- 采样参数（temperature、max_tokens、seed 等）
- 数据集路径、split、**固定的 revision**（本地任务集用内容指纹），以及抽样后样本 id 列表的哈希
- Solver 指纹（prompt 模板、few-shot 设置，或 Agent 的系统提示、工具、步数上限）和 Scorer 指纹
- harness 的 git commit、工作区是否有未提交修改、Python 版本、关键依赖版本、`uv.lock` 的哈希
- 实际沙箱镜像与资源身份、服务端报告的模型标识、epoch 与缓存策略；Judge 任务另记裁判身份、提示词与评分规则

## 诊断指标

每道题都会被归为以下一类，而不只是"对/错"：

| outcome | 含义 |
|---|---|
| `correct` | 答对或测试通过 |
| `wrong` | 抽到了答案但答错，或测试未通过 |
| `extract_failed` | 没能从输出中抽取出答案，通常是格式问题而不是能力问题 |
| `truncated` | 输出因 `max_tokens` 被截断 |
| `step_limit` | Agent 用完了步数上限仍未完成 |
| `time_limit` | Agent 用完了单题总时长（任务参数 `time_limit`）仍未完成；超时后仍会评分 |
| `token_limit` | Agent 用完服务端报告的逻辑 token 预算后停止；最后一次调用允许超额，停止后仍评分 |
| `api_error` | 模型调用失败（重试后仍失败），计入分母，避免虚高 |
| `solver_error` | Solver / Agent 代码本身出错，不是模型的问题 |
| `scorer_error` | 评分器出错、标准答案无效，或打出了无效分数（NaN、不在 0～1 之间），不是模型的问题 |
| `judge_error` | 裁判调用失败、回复截断或判断格式无效；原始回答与裁判记录保留 |
| `env_error` | 沙箱环境故障（镜像构建失败、容器异常等），不是模型的问题 |

所有失败都计入分母，但会归到真正出错的一方，避免把平台或环境的问题算成模型能力不足。

此外还会统计：

- **抽取方式分布**：答案是按 `The answer is`、`####`、`\boxed{}` 还是兜底规则抽出来的，用来判断模型是否遵守了要求的输出格式。
- **Agent 统计**：结束原因（调用 submit、直接文字回复、步数用完）、平均步数、执行命令数和失败命令数。

## 实测示例

在 Ollama 上用 `qwen2.5:0.5b-instruct`（CPU）运行 `configs/ollama-smoke.yaml` 的结果：

| task | n | accuracy | 95% CI | correct | wrong | extract_failed | truncated |
|---|---|---|---|---|---|---|---|
| gsm8k | 20 | 0.450 | [0.25, 0.65] | 9 | 9 | 0 | 2 |
| mmlu | 40 | 0.425 | [0.275, 0.575] | 17 | 22 | 0 | 1 |

- **格式不遵守**：prompt 要求以 `The answer is <number>` 结尾，但 GSM8K 有 18/20 的回答用了 `\boxed{}`。
- **样本量太小时置信区间很宽**：20 道题的 95% 置信区间横跨 40 个百分点。
- **指标抖动**：temperature 改成 0.8 再跑一次，准确率仍是 0.450，但有 2 道题由对变错、2 道题由错变对。
- **缓存续跑**：相同配置第二次运行全部命中缓存，耗时从约 70 秒降到约 3 秒。默认策略 `auto` 只在 temperature 为 0 时复用缓存，采样运行（temperature > 0）每次都重新调用模型；可用 `--cache-policy always|never` 改变，manifest 中记录每个任务实际是否使用了缓存。

在 3 个自建终端任务上，Oracle 3/3 通过；`qwen2.5:3b-instruct` 通过 1/3。从轨迹能直接看出失败原因：它在 `count-errors` 上用 `grep -c ERROR` 得到 211（正确答案 96，有些 INFO、WARN 消息正文里也含 ERROR），在 `fix-mean` 上连续执行了 7 次没有实际改动的 `sed` 就提交了。`qwen2.5:0.5b-instruct` 则是只创建了空文件，却回复说已写入内容。

## 测试

```bash
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

2026-10-25 在 `f3aebaf` 上本机复核为 **323 passed、18 skipped**，静态检查通过；开发方完整环境记录为 358 passed，未在本机独立复核。通过数与跳过数受可选依赖、Docker 和任务资源影响，具体见进度记录。

单元测试用 MockModel、冻结本地数据和假沙箱，不需要网络或密钥；onboarding 测试用子进程验证可信的脚本化候选，容器隔离用例需要 Docker。Docker 集成测试需要 daemon、镜像和题目资源，不可用时跳过；可选依赖缺失只跳过相应测试。

## 路线图

| 阶段 | 实现与验收状态 |
|---|---|
| 1～4 基础评测平台 | 已实现，已有问答运行记录 |
| 5 Agent 与沙箱 | 主链路已实现，真实任务上的 Solver 对照待验证 |
| 6 模型裁判 | 最小实现与 J1／J2 补修通过本轮回归；真实校准待验证 |
| 7 统计与诊断 | 已实现，真实多 epoch 与中断恢复待验证 |
| 8 自动接入 | 第一版已实现，本轮评审有三项待补修；真实接入待验证 |
| 9 真实评测集与 Agent 集成 | 最小集成穿插在阶段 5；完整实验验收待补齐 |
| 10 Harness 消融 | 预算、协议与对照工具已实现；真实单变量消融待运行 |

阶段编号与 progress 轮次编号不同。下一步优先补齐评审问题，并让本地试运行与有边界的开发并行。
