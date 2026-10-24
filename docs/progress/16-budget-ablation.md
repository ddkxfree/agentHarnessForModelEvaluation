# 16 · 阶段十最小版：token 预算、Solver 协议记录、预算敏感性与机制消融的区分

日期：2026-10-24。对应 [05](05-review-correctness-diagnostics.md) 中阶段 10 的最小验收和 [11](11-stage5-review-and-next.md) 第 3 条："补 token 预算与能力／协议记录；做一次真实单变量消融。预算不同时明确属于预算敏感性实验，不声称是等预算策略对照。"

本轮只用 MockModel／FakeProvider 测试，没有在云端启动 Ollama。**真实单变量消融尚未运行，状态为待验证**，配置和命令见第 4 节，需在本地或通过外部 API 执行。

## 1 token 预算

- `Task.token_limit`：单个样本上所有模型调用的输入加输出 token 上限，默认 `None`（不限）。`terminal`、`terminal-bench`、`swebench` 三个任务都新增参数 `token_limit`，例如 `-p terminal.token_limit=40000`。
- 在 runner 包给 solver 的 `generate` 里执行：每次调用前检查已用 token，达到上限就拒绝这次调用，抛出 `TokenLimitReached`。所以最后一次被允许的调用可能让总量略超上限，超出部分不会多于一次调用的用量。
- `TokenLimitReached` 继承 `BaseException`，不会被 solver 的 `except Exception` 吞掉。mini-swe-agent 在工作线程里调用模型，异常会经桥接返回到工作线程，再从 `DefaultAgent.run` 抛出。runner 捕获后和时间上限一样处理：保留已有轨迹，照常评分。没解出的样本 outcome 为新增的 `token_limit`，`finish_reason` 也是 `token_limit`；如果到上限时环境已经满足测试，仍判为正确。
- 从缓存回放的响应也计入预算，所以恢复运行会在原运行停下的位置停下。
- manifest 的任务条目新增 `token_limit`；summary 的 outcome 计数自动包含 `token_limit`。

## 2 Solver 协议记录

- Solver 可选实现 `protocol()`，返回它和模型交互的方式：
  - `interface`：交互接口，如 `native_tool_calls`、`single_turn`、`reference_solution`；
  - `tools`：提供给模型的工具；
  - `finish`：怎样算结束；
  - `step`：一步包含什么；
  - `observation`：模型看到的命令输出，如截断方式；
  - `requires`：对模型和任务的要求，如原生工具调用、沙箱。
- 结果写入 manifest 的 `solver_protocol`。它不进入 solver 指纹，所以已有缓存和 `describe()` 不变。
- 已实现 `protocol()` 的 Solver：
  - `BasicAgent`：记录截断长度。
  - `MiniSweAgent`：说明不含有效工具调用的回复会收到格式错误提示并算作一步，连续多少次后结束。这就是 [10](10-solver-comparison.md) 里"两种 Solver 的步定义不同"的来源。
  - `PromptSolver`。
  - 三个 Oracle（终端任务、TB、SWE-bench 参考补丁），记为"无模型调用"。
- `harness compare` 的 solver 对照会在 `config_changes_vs_baseline` 中列出 `solver_protocol` 的差异。

## 3 compare 报告：预算与实验类型

- 每个条件新增 `budgets`，从该条件第一次运行的 manifest 读取，即实际生效的值：
  - `max_steps` 和 `command_timeout` 取自任务参数；
  - `time_limit` 和 `token_limit` 取自任务条目。
- 报告新增 `equal_budget`：所有条件的预算都相同。
- 报告新增 `experiment_type` 和 `experiment_note`。消融条件改动的参数属于预算类（`max_steps`、`time_limit`、`token_limit`、`command_timeout`）时，类型为 `budget_sensitivity`，否则为 `mechanism_ablation`：
  - `solver_comparison`：Solver 对照，差异属于整个 Agent；
  - `budget_sensitivity`：只说明结果对预算的敏感程度，不说明哪种机制更好；
  - `mechanism_ablation`：预算相同、只改一个机制，差异可以在统计不确定性范围内归因于该机制；
  - `mixed`：同一对照里两类条件都有，须分开解读。
- 每个条件也有自己的 `experiment_type`；基线条件为 `None`。
- 每次运行和每个条件新增 `steps_without_tool_calls`：没有调用工具的步数，用于观察格式问题，例如 [13](13-statistics-diagnostics.md) 中 mini 复制示例行的情况。
- 命令行输出实验类型、各条件预算；solver 对照或机制消融的预算不一致时给出黄色提示。
- `harness compare` 新增 `-m／--model` 和 `--base-url`，可以不改 YAML 就换成外部 API 运行同一份对照。覆盖后的模型记录在 `index.json` 的 spec 中。`--resume` 不允许换模型。
- 任务参数 `max_observation_chars`：
  - 三个任务都可设置，只对 `solver=agent` 生效；
  - 选了其他 Solver 时报错，不会被静默忽略；
  - 不设置时保持原默认值：终端任务和 TB 为 4000，SWE-bench 为 8000。

## 4 待验证的真实运行

两份配置都已通过加载和分类测试，但还没有用真实模型运行。

### 机制消融：观察截断长度

`configs/ablation-observation-terminal-bench.yaml` 在 5 道固定版本的 Terminal-Bench 2.0 题上比较 BasicAgent 的两个条件：

- 命令输出截到 4000 字符；
- 命令输出截到 500 字符，首尾各保留一半。

两个条件的模型、样本、种子、`max_steps=30`、`time_limit=900`、`command_timeout=120`、`token_limit=200000` 都相同，每个条件重复 3 次。

```bash
harness run -m none/none -t terminal-bench -p terminal-bench.solver=oracle   # 先确认 5/5
harness compare -c configs/ablation-observation-terminal-bench.yaml          # 本地 Ollama
harness compare -c configs/ablation-observation-terminal-bench.yaml -m openai/gpt-4o-mini   # 外部 API
harness compare-report runs/compare-...
```

验收要求：

- `experiment_type` 为 `mechanism_ablation`；
- `equal_budget` 为真；
- `scores_comparable` 为真；
- `config_changes_vs_baseline` 只涉及 `max_observation_chars` 及其派生项：任务参数、solver 描述和指纹、协议中的截断说明。

只有 5 道题，配对差会标注样本不足、不能下结论，需要结合 `per_sample_solved` 和轨迹解读。

### 预算敏感性：token 上限

`configs/budget-tokens-terminal.yaml` 在 3 道自建终端任务上比较 `token_limit` 40000 和 8000 两个条件。

```bash
harness compare -c configs/budget-tokens-terminal.yaml          # 本地 Ollama
harness compare -c configs/budget-tokens-terminal.yaml -m openai/gpt-4o-mini
```

验收要求：

- `experiment_type` 为 `budget_sensitivity`；
- `equal_budget` 为假；
- 小预算条件中，因预算停止的样本 outcome 为 `token_limit`。

## 5 边界

- token 计数使用服务端返回的 usage。服务端不返回 usage 时计为 0，预算不会生效。Ollama 的 OpenAI 兼容接口会返回 usage，其他服务需要确认。
- 预算按单个样本计算，不限制整个运行的总花费。
- `experiment_type` 依据 spec 中改动的参数名判断，参数名必须是这四个预算键之一。自定义任务如果用别的名字表示预算，例如 `step_budget`，会被归为机制消融。
- 预算是否相同，比较的是 manifest 中记录的值，前提是任务把 `max_steps`、`command_timeout` 写进 `params`。内置任务都写了。
- 任务参数多了 `token_limit` 和 `max_observation_chars`，manifest 多了 `token_limit` 和 `solver_protocol`。用 `harness diff` 比较本轮之前的运行时，这些字段会显示为新增差异，属于记录项增加，不是配置变化。

## 验证

`uv run pytest -q`：252 passed（本机 Docker 实际运行）；`ruff check`、`ruff format --check` 通过。新增测试在 `tests/test_budget.py`：

- BasicAgent 在第 4 次调用被拒，测试分别为未通过和已通过两种情况；
- 不设预算时 BasicAgent 跑到步数上限；
- 回放的调用计入预算；
- mini-swe-agent 在工作线程中被预算停止，保留 3 条 assistant 消息；
- manifest 中的 `token_limit` 和 `solver_protocol`；
- 各 Solver 的协议，以及协议不影响指纹；
- 任务参数传递，以及参数用在非 `agent` Solver 上时报错；
- 预算敏感性、机制消融和混合三种对照的分类，以及命令行输出；
- 新配置和已有对照配置的加载与分类。
