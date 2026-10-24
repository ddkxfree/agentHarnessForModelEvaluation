# 17 · 阶段十复评：预算并发边界、实验归因与可选依赖测试

日期：2026-10-24。评审阶段十分支 `origin/cursor/stage10-budget-ablation-7316` 的 `b45bf80`，含实现提交 `44037ff`、配置 `5565ec4` 和文档 progress/16。缓存的 PR #16 合并提交 `6297e5b` 与该分支的 src／tests 无差异。

当前工作区 HEAD 为 `2f5f69b`（阶段七补修）。本轮导出阶段十到临时目录检查，不切换用户分支，不修改实现，不调用真实模型。

## 总体结论

阶段十最小功能已实现：逻辑 token 预算、停止后照常评分、Solver 协议记录、预算敏感性与机制消融分类、两个待运行配置。progress/16 明确真实单变量消融未运行，该状态准确；不能称完整实验验收已完成。

复评仍有 T1～T3 需要补修。既有 BasicAgent／mini 串行路径的预算控制已有测试；下面的预算并发复现针对公共 Generate 接口的自定义 Solver。

## 验证范围

- 最新阶段十导出源码：**216 passed、3 failed、16 skipped**。
- 三个失败均因 tests/test_budget.py 新用例无条件构造 mini Solver，而本机缺少可选依赖 minisweagent。
- Docker 和可选依赖相关测试未执行；开发文档中的 252 passed 未在本机独立复核。
- ruff check、ruff format --check 通过（83 files already formatted）。
- 上轮阶段七补修 S1～S3 对应的现有回归测试在本次阶段十快照中没有失败；本次不另行宣称完成真实运行验收。

## T1 [P2] 并发 generate 会突破文档声称的“一次调用超额”边界

位置：阶段十 `src/harness/core/runner.py:146-176`。

预算在请求前检查 spent，但等请求返回才累加用量。若一个样本的 Solver 同时发出多个 generate，所有调用都可能在 spent=0 时通过检查。

本机用自定义 Solver 并发发出 5 个请求，每次返回 100 token，token_limit=50，结果：

```text
fresh calls = 5
tokens spent = 500
outcome = correct
```

串行软预算允许一次请求超额，这是已声明的设计，本次不要求把它改成精确的硬上限。问题是公共接口并发使用时，超额可能达到多次调用的量，和文档不一致。

验收：明确并强制每样本的预算并发语义，例如串行化“检查／调用／记账”，或实现预算预留；添加并发生成回归测试。异常、取消和缓存返回路径都应释放锁／预留。未知 usage 和单调用超额仍应明确记录，不能把该预算当作严格的金额上限。

## T2 [P2] 实际预算不等，仍可能输出“等预算机制消融”的归因说明

位置：`analysis/compare.py::experiment_type` 和 build_report 的 experiment_note／equal_budget。

实验类型仅由条件参数名判断，实际预算另外计算，却不约束归因说明。一个任务参数可以同时改变机制及其有效预算；当前报告只警告预算不等，仍给出可归因于单一机制的说明。

本机使用单参数 prompt 的 Task factory，careful 条件派生 token_limit=100，基线为 1000：

```text
experiment_type = mechanism_ablation
equal_budget = False
scores_comparable = True
experiment_note = Conditions change one mechanism ... under the same budgets,
                  so differences can be attributed to that mechanism ...
```

当前内置观测截断配置不会触发这项派生预算变化，但公共分类逻辑不应输出与实际记录矛盾的结论。文档已承认未知预算参数名的限制，仍需让报告避免无条件归因。

验收：把“请求的实验类型”和“根据实际记录可支持的解释”分开；实际预算不等、运行不完整、环境漂移或出现额外配置变化时，不输出等预算单机制因果说明。保留原始差异与数字供查看。对派生预算变化、未知预算字段／缺失预算记录增加测试。

## T3 [P2] 基础安装测试因可选依赖缺失而失败

位置：tests/test_budget.py 中以下用例：

- test_token_limit_stops_mini_swe_agent；
- test_every_builtin_solver_states_its_protocol；
- test_shipped_configs_are_valid_and_classified 的 compare-terminal-ollama.yaml 参数项。

这三个测试没有像已有 tests/test_mini_swe_agent.py 那样检查可选依赖。在只安装基础开发依赖的环境下，pytest 直接失败。

验收：仅跳过需要 mini 的测试／参数项，保留 BasicAgent、Oracle 和配置静态分类的覆盖；分别验证基础依赖与 swebench extra 环境。不要因为可选依赖缺失而跳过整份预算测试。

## 文档与下一步

- progress/16 的“真实消融待验证”必须继续保留；配置加载成功不等于真实消融完成。
- 协议描述是 Solver 对模型的要求，不是模型服务实际能力检测；工具调用能力仍需冒烟确认。
- 先补 T1～T3 的小 PR，继续用 MockModel 测试；之后可开发阶段六的最小 Judge，再开发阶段八适配 Agent。
- 真实实验在本地或外部 API 执行，先少量样本和一次重复确认链路，再扩为三次重复，不需要在云端 CPU 上反复跑 3B。
- 后续阶段的通俗阅读入口见 [后续阶段说明](../next-stages.md)。
