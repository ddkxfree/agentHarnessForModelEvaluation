# agentHarnessForModelEvaluation

这是一个接入主流模型评测的 agent，进行大模型评测的平台。

目前已完成：

- **平台内核**：用 GSM8K 和 MMLU 跑通"加载数据 → 构造 prompt → 调模型 → 抽取答案 → 打分 → 统计 → 归档"的完整链路，具备并发、重试、缓存续跑、可复现记录、离线重打分和运行对比能力。
- **Agent 评测**：每道题一个 Docker 容器，Agent 通过工具调用在容器里执行命令，结束后由测试脚本检查容器状态来判分；完整记录 Agent 每一步的轨迹。
- **SWE-bench 与 mini-swe-agent**：接入 SWE-bench Lite（官方镜像和判分逻辑）；mini-swe-agent 作为可替换的 Solver 插件，和基线 Agent 在同一批题上对比。

**想在自己电脑上跑起来，看 [上手练习指南](docs/hands-on.md)。** 每一轮工作的详细记录见 [`docs/progress/`](docs/progress/README.md)，后续计划见 [`docs/roadmap.md`](docs/roadmap.md)。

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
harness diff runs/<a> runs/<b>                       # 对比两次运行：配置/环境差异 + 逐样本翻转
harness sandbox-cleanup                              # 清理异常退出后残留的容器
```

`-p task.key=value` 的值按 YAML 解析，例如 `-p mmlu.subjects=[anatomy,virology]`。

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
├── tasks/            gsm8k.py、mmlu.py、terminal.py、swebench.py，以及 terminal_data/ 下的终端任务
├── analysis/         stats（bootstrap 置信区间、Agent 统计）、rescore、diff
└── cli.py
```

几个核心概念：

- **Task** = 数据集 + `Solver` + `Scorer` + 参数，需要执行环境的任务再加一个沙箱声明。
- **Solver** 拿到的是 runner 包装好的 `generate` 函数（已带缓存、重试、超时），Agent 循环可以多次调用模型；需要沙箱的任务还会拿到这道题的容器。
- **Scorer** 分两类：普通 Scorer 只依赖模型原始输出，可以离线重打分；`SandboxScorer` 在容器里判分，例如运行测试脚本。
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
│   ├── samples.jsonl      每道题一行：样本、完整 messages、原始输出、Agent 轨迹、token、耗时、打分、outcome
│   └── summary.json
└── ...
```

`manifest.json` 记录了复现一次评测需要的全部信息：

- 模型 provider、名称、base_url（不含 API key）
- 采样参数（temperature、max_tokens、seed 等）
- 数据集路径、split、**固定的 revision**（本地任务集用内容指纹），以及抽样后样本 id 列表的哈希
- Solver 指纹（prompt 模板、few-shot 设置，或 Agent 的系统提示、工具、步数上限）和 Scorer 指纹
- harness 的 git commit、工作区是否有未提交修改、Python 版本、关键依赖版本、`uv.lock` 的哈希

## 诊断指标

每道题都会被归为以下一类，而不只是"对/错"：

| outcome | 含义 |
|---|---|
| `correct` | 答对或测试通过 |
| `wrong` | 抽到了答案但答错，或测试未通过 |
| `extract_failed` | 没能从输出中抽取出答案，通常是格式问题而不是能力问题 |
| `truncated` | 输出因 `max_tokens` 被截断 |
| `step_limit` | Agent 用完了步数上限仍未完成 |
| `api_error` | 模型调用失败（重试后仍失败），计入分母，避免虚高 |
| `solver_error` | Solver / Agent 代码本身出错，不是模型的问题 |
| `scorer_error` | 评分器出错、标准答案无效，或打出了无效分数（NaN、不在 0～1 之间），不是模型的问题 |
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
- **缓存续跑**：相同配置第二次运行全部命中缓存，耗时从约 70 秒降到约 3 秒。

在 3 个自建终端任务上，Oracle 3/3 通过；`qwen2.5:3b-instruct` 通过 1/3。从轨迹能直接看出失败原因：它在 `count-errors` 上用 `grep -c ERROR` 得到 211（正确答案 96，有些 INFO、WARN 消息正文里也含 ERROR），在 `fix-mean` 上连续执行了 7 次没有实际改动的 `sed` 就提交了。`qwen2.5:0.5b-instruct` 则是只创建了空文件，却回复说已写入内容。

## 测试

```bash
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

135 个测试。单元测试使用 `MockModel`、内存数据集和内存版假沙箱，不需要网络；`tests/test_docker_sandbox.py` 中的集成测试需要 Docker（SWE-bench 的还需要本地已拉取镜像），没有时自动跳过。集成测试会检查每个终端任务和 SWE-bench 题目都能被 Oracle 解出，且什么都不做的 Agent 一定失败。

## 路线图

- [x] 阶段 1～4：平台内核（GSM8K、MMLU、并发/重试/缓存、可复现 manifest、诊断统计、rescore、diff）
- [ ] 阶段 5：Agent 评测和 Docker 沙箱
  - [x] 沙箱抽象与 Docker 实现、`env_error`、基线 Agent、轨迹记录、3 个自建终端任务、Oracle 校验
  - [x] 接入 SWE-bench Lite，mini-swe-agent 作为 Solver 插件
  - [ ] 接入真实的 Terminal-Bench 任务
- [ ] 阶段 6：LLM-as-Judge（交换位置去偏、多裁判投票）
- [ ] 阶段 7：多次运行的方差分析、错误自动归类报告
- [ ] 阶段 8：用 Agent 自动接入新评测集
