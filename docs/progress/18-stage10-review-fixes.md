# 18 · 阶段十补修：预算并发、实验归因、可选依赖测试

日期：2026-10-24。对应 [17](17-stage10-review.md) 的 T1～T3。只用 MockModel 测试，没有调用真实模型；**真实单变量消融仍未运行，状态仍为待验证**。

## 合并说明

PR #16（阶段十）的目标分支是阶段七补修分支，合并时 #15 已先合入 master，所以 #16 只进了阶段七补修分支，master 上没有阶段十的源码。本轮从 master 新建分支，先合并阶段十分支，解决 `docs/progress/README.md` 中 16 与 17 两行的冲突，再做补修，一起提交到 master。

## T1 并发调用突破预算

- 原因：预算在请求前检查，请求返回后才累加用量。Solver 同时发出多个调用时，它们都能在已用 0 token 时通过检查。
- 修复：设置了 `token_limit` 时，每个样本有一把 `asyncio.Lock`，"检查预算、调用模型或读缓存、记账"在锁内依次进行。
  - 同一样本的并发调用因此串行执行，最多只有一次调用超出预算。
  - 不设预算时不加锁，并发行为不变。
  - 锁由 `async with` 管理，调用出错、被取消（如单题时限）或命中缓存时都会释放。
- 边界不变：
  - 预算按服务端报告的 usage 计算，不报告 usage 时不生效；
  - 最后一次被允许的调用可能超出预算；
  - 它不是严格的费用上限。`Task.token_limit` 的注释已写明这几点。
- 测试：
  - `test_concurrent_calls_cannot_all_pass_the_budget_check`：自定义 Solver 并发发出 5 个调用，每个返回 100 token，预算 50。结果只有 1 次实际调用，另外 4 次被拒。临时去掉锁后，同一测试得到 5 次调用，与复评复现一致。
  - `test_a_failed_call_releases_the_budget_lock`：3 个并发调用中第一个出错，后两个照常完成。

## T2 实际预算不等时仍给出机制归因说明

报告把两件事分开：

- `experiment_type`：spec 请求的实验类型，含义不变，按条件改动的参数名判断。
- `interpretation`：记录能支持的解释。
  - `supported` 为请求的类型（或 `mixed`），出现下列任一问题时为 `inconclusive`；
  - `reasons` 列出全部原因，每个条件也有自己的 `interpretation`；
  - `experiment_note` 改为描述 `supported`，`inconclusive` 时说明差异不能归因于改动的参数，数字只供查看。

判为 `inconclusive` 的情况：

- 运行不完整、样本不一致、同一条件的重复配置不同、沙箱环境漂移；
- 预算记录缺失：条件没有运行记录，或 manifest 是加入 token 预算之前写的；
- 实际预算与基线不同。预算敏感性实验只允许它改动的那个预算不同；机制消融和 Solver 对照不允许任何预算不同。这覆盖了"一个参数同时改变机制和有效预算"的情况；
- 改动的参数名看起来像预算（含 `budget`、`limit`、`timeout`、`steps`），但不在报告追踪的四个预算键里，无法确认预算相同；
- 除改动参数外还有其他配置差异（任务条目以外的 manifest 字段，如模型、生成参数、harness 版本）。

任务条目内的差异（solver 描述、指纹、协议、缓存命名空间）都由同一个任务工厂根据改动的参数生成，视为这个参数的结果；其中派生出的预算变化由上一条单独检查。

命令行先输出请求的类型和支持的解释，再逐条列出原因和各条件的预算。原始差异、配对统计和 `config_changes_vs_baseline` 仍完整输出。

测试（`tests/test_budget.py`）：

- 派生预算：`prompt=capped` 同时把 token 预算从 1000 降到 100，请求为机制消融，解释为 `inconclusive`，配对统计仍然给出；
- 未追踪的预算参数 `step_budget`；
- 删掉 manifest 中的 `token_limit`，模拟旧记录；
- 改动生成温度；
- 运行未完成；
- 正常的机制消融和混合对照仍按请求类型解释。

## T3 缺少可选依赖时测试失败

- 需要 mini-swe-agent 的用例改为在 `minisweagent` 不可用时跳过：mini 的预算停止、mini 的协议。
- BasicAgent、Oracle、PromptSolver 的协议测试拆出来，不再依赖 mini。
- 配置分类测试仍对三份配置做静态分类，只跳过构造 mini Solver 那一步。
- "选了 mini 却设置 `max_observation_chars` 时报错"的检查在导入 mini 之前执行，基础环境下也会运行。

## 验证

- 完整环境（含 swebench extra，本机 Docker 实际运行）：`uv run pytest -q` 260 passed。
- 只装基础依赖（`UV_PROJECT_ENVIRONMENT=/tmp/basevenv uv sync`，未装 extra）：235 passed、8 skipped。跳过的全是需要 minisweagent／swebench 的用例，其中 `test_budget.py` 跳过 2 个。
- `ruff check`、`ruff format --check` 通过。

## 真实消融：要不要部署模型

需要调用真实模型，但不需要自己部署：

- 外部 API 方案：模型在服务方推理，Harness 和 Docker 任务容器在本机运行。用 `harness compare -c configs/ablation-observation-terminal-bench.yaml -m <provider>/<model>`，API key 通过环境变量提供，如 `OPENAI_API_KEY`、`DASHSCOPE_API_KEY`、`DEEPSEEK_API_KEY`。
- 本地 Ollama 方案：需要下载并启动模型。
- 建议先用 1 次重复、少量题确认工具调用链路，再扩到 3 次重复。
