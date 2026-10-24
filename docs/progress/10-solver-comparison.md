# 10 · Solver 对照（第五阶段加固，第五轮）

日期：2026-10-24。在 09（真实 Terminal-Bench）的基础上开发。**待 Codex 复评。**

## 目标

05 复评对阶段 10 的最小要求：固定同一模型、样本、种子和预算；每个条件重复 3 次，输出成功率、tokens、耗时和失败归类；不同原生 Solver 的比较作为独立对照，不冒充单变量消融。

## 这一轮做了什么

### 1. `harness compare` 与 `harness compare-report`

新增 `analysis/compare.py`，配置为一个 YAML（示例见 `configs/compare-terminal-ollama.yaml`）：

- `kind` 只能是以下两种：
  - `solver_comparison`：各条件只能设置 `solver`，且各不相同。报告附说明：差异属于整个 Agent（提示词、工具、输出处理），不是单一机制；
  - `ablation`：第一个条件为基线，不覆盖参数；其余条件相对基线恰好改一个参数，且不能是 `solver`。
- 校验在加载配置时进行，不合规直接报错。
- `repeats > 1` 且缓存会被复用时（`always`，或 `auto` 且 temperature = 0）拒绝运行，否则重复只是回放缓存。默认 `cache_policy: never`。
- 运行顺序按重复轮次交错（basic#1、mini#1、basic#2……），让负载、镜像缓存等漂移对各条件影响相同。
- 每次运行都是普通的运行目录，`show`、`diff`、`rescore` 照常可用；`index.json` 逐次写入，中断后已完成的运行保留。
- `compare.json`（`compare-report` 可离线重建，不调模型）包含：
  - 每个条件：逐次运行的准确率、解出数、输入／输出 tokens、墙钟时间、平均步数、结果分类、结束原因、缓存命中数、服务端模型标识；以及准确率均值／最小／最大；
  - 每个样本在各条件下的解出次数（如 `2/3`）；
  - `config_changes_vs_baseline`：与基线首次运行的 manifest 差异，即实际改变了哪些配置；
  - `repeat_config_changes`：同一条件的各次重复之间的配置差异（应为空）；
  - `complete`（运行齐全且都完成）、`same_samples_in_every_run`。

### 2. 统计口径补充（runner 与 summary）

- `SampleResult.wall_time_s`：从创建沙箱到清理完成的秒数，不含等待并发名额的时间。
- `SampleResult.usage()`：有步骤记录时按步骤累加 tokens。之前 summary 只统计 `output.usage`，Agent 中途失败（没有 output）的样本 tokens 记为 0；现在也计入。
- summary 新增 `wall_time_s: {total, mean}`。

## 验证

`uv run pytest -q`：210 passed（本机 Docker 实际运行）。`ruff check`、`ruff format --check` 通过。新增 `tests/test_compare.py`（19 个用例）：

| 测试 | 验收项 |
|---|---|
| `test_conditions_are_repeated_and_reported_side_by_side` | 2×2 次运行、模型调用 16 次（无缓存回放）、成功率／tokens／分类／逐样本解出次数、配置差异只有 solver |
| `test_report_is_rebuilt_offline_and_flags_drift` | 离线重建结果一致；篡改某次重复的 temperature 后被列出；中断的运行使 `complete` 为假 |
| `test_runs_on_different_samples_are_flagged` | 样本集合不一致可发现 |
| `test_invalid_comparisons_are_rejected`（9 例）、`test_valid_comparisons`（4 例） | 对照／消融规则与缓存规则 |
| `test_ablation_changes_one_parameter` | 消融流程 |
| `test_tokens_include_steps_of_a_failed_solver`、`test_samples_record_wall_time` | 统计口径 |

## 实测：qwen2.5:3b-instruct（Ollama，CPU）在 3 个自建终端任务上

命令：`harness compare -c configs/compare-terminal-ollama.yaml`。固定 temperature 0、seed 0、max_tokens 512、每题最多 10 步、单命令 60 秒、单题 600 秒、并发 1、不用缓存，每个条件 3 次。结果目录：`runs/compare-20261024-092848-terminal/`（不入库）。

| 条件 | 准确率均值（最小–最大） | 每次运行输入 tokens | 输出 tokens | 每次运行墙钟秒 | 平均步数 | 结果 | 结束原因 |
|---|---|---|---|---|---|---|---|
| basic（BasicAgent） | 0.333（0.33–0.33） | 5831 | 1228 | 106 | 3.6 | correct 3、wrong 6 | submit 9 |
| mini（mini-swe-agent） | 0.111（0.00–0.33） | 20458 | 1958 | 182 | 5.7 | correct 1、wrong 8 | RepeatedFormatError 8、max_steps 1 |

逐题解出次数：`hello-file` basic 3/3、mini 1/3；`count-errors`、`fix-mean` 两者都是 0/3。

观察：

- mini-swe-agent 的失败几乎都是 `RepeatedFormatError`：3B 模型不能稳定输出 mini 要求的单个 bash 代码块格式。这是"模型 + 协议"的组合结果，不能说明 mini 的策略更差；需要更强的模型才能比较策略本身。
- temperature 0、seed 0 下，同一条件的 3 次重复步数和耗时仍不同（如 basic 的 `fix-mean` 为 7／7／4 步），Ollama 在 CPU 上并非逐位确定。因此重复是有意义的。
- `config_changes_vs_baseline` 列出 mini 与 basic 的全部差异（提示词模板、步数上限的实现、观测截断、环境变量等），印证这是整体对照而非单变量消融。
- 样本只有 3 个，不做显著性结论；置信区间和多轮统计留到阶段 7。

## 为什么慢

- 模型在 4 核 CPU 上运行（无 GPU），单次调用约 4～20 秒，上下文越长越慢；mini 的提示词更长（输入 tokens 约为 basic 的 3.5 倍）。
- 为了让墙钟时间可比，并发设为 1；本地 Ollama 本来也一次只处理一个请求。
- 本次 18 个样本约 13 分钟。同样配置的 Terminal-Bench 对照（`configs/compare-terminal-bench-ollama.yaml`，5 题 × 2 × 3、每题 15 步）预计约 1 小时，**本轮未运行**。

## 待复评要点与遗留

- 尚未在 Terminal-Bench 和 SWE-bench 上跑对照；3B 模型在这些题上大概率全为 0，需要 API 模型（如 `dashscope/qwen-plus`）才有区分度。
- 没有 token 预算上限，只有步数、单命令和单题时间预算；两种 Solver 的"步"定义略有不同（mini 的格式错误也算一步）。
- `ablation` 只有单元测试，还没有真实运行。
