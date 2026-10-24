# 19 · 阶段十补修复评与阶段六入口

日期：2026-10-24。基准：master `21e9665`（PR #17，补修实现 `a1d5cdb`）。本轮只评审、复现和更新文档，不修改实现，不调用真实模型。

## 结论

- T1：预算检查、调用和记账使用每样本 asyncio.Lock 串行保护，并发和异常释放回归测试通过。
- T3：mini 相关用例按可选依赖跳过；其余预算、协议和配置分类测试继续执行。基础环境不再出现上轮的三个失败。
- T2：预算不等、旧预算记录缺失和部分额外配置变化会降级为 inconclusive；但任务条目内的额外变化仍被整体忽略，尚未完全通过。
- 下一阶段确实是阶段六最小 Judge。T2 留一个小补修，不需重做阶段十，也不需等真实消融才开始阶段六设计和实现。真实消融保持待本地／API 验证。

## 验证

- `UV_CACHE_DIR=/private/tmp/harness-review-uv-cache uv run --no-sync pytest -q -rs`：**225 passed、18 skipped**。
- ruff check、ruff format --check 通过（86 files already formatted）。
- 跳过涉及 Docker、题目文件／镜像和可选依赖；本机没有独立复核开发文档中的完整 260 passed。

## T2 残留 [P2] 跨条件的任务内额外差异被整体忽略

位置：`src/harness/analysis/compare.py:365-371`。

`_extra_changes` 把所有 `tasks.*` 差异都排除，假定它们来自被改变的参数。但任务条目也包含实际生成配置、scorer 和数据集版本，不能全部视为允许的派生变化。

复现方法：用现有 `tests/test_budget.py::mechanism_dir` 生成正常对照，再使用其 rebuild 辅助函数，分别修改 careful 条件的下列一个字段，重建报告：

| 修改字段 | 修改示例 | 实际报告 |
|---|---|---|
| tasks[0].generate_config.temperature | 改为 0.7 | supported=mechanism_ablation，reasons=[] |
| tasks[0].scorer.fingerprint | 改为不同评分规则指纹 | 同上 |
| tasks[0].dataset.revision | 改为不同数据集版本 | 同上 |

三种情况下 scores_comparable 也均为 True。原始差异仍有记录，但新的 interpretation 无法阻止错误归因。

补修验收：按字段明确允许当前参数带来的预期变化，不能排除整个 tasks 子树。等模型机制消融要求模型生成配置、数据集／样本内容版本和评分规则一致；命中这些额外变化必须标为 inconclusive。保留正常观测截断消融的合法派生项：该参数、solver 描述／指纹、协议中的截断说明，以及条件独立缓存命名空间。

另一个同函数的状态一致性问题可顺手补上：运行 interrupted 时，总报告为 inconclusive，但 conditions.careful.interpretation 仍可能是 mechanism_ablation。逐条件解释应纳入适用于该条件的全局阻断原因，或明确命名为“仅条件自身检查”。

新增回归测试可直接在已有 MockModel 生成的结果上改变这些记录，不需要启动模型。

## 下一步

1. 一个小提交补 T2 的字段检查和报告层级一致性，再复评。
2. 按 [阶段六开发与验收范围](../stage6-plan.md) 实现最小 Judge。
3. 保留阶段十真实消融、阶段六真实裁判校准为独立运行验收；云端继续使用 MockModel 开发测试。
