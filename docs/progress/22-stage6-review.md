# 22 · 阶段六最小 Judge 复评验收

日期：2026-10-25。基准：master `881036b`（PR #19，含阶段六实现和阶段十 T2 补修）。本轮只评审、运行测试和复现、更新文档，不修改实现，不调用真实模型。

## 验收结论

最小 Judge 主体已实现且现有测试通过；J1、J2 两项需要小补修后再完成最小功能验收。真实裁判可靠性仍需独立人工标签与真实运行验证。

| 验收范围 | 结论 |
|---|---|
| rubric 分项评分、分数合法性、固定归一化 | 已实现，MockModel 测试通过 |
| A/B 双序映射、明确平局与不稳定分开 | 已实现；只与参考答案比，两个历史运行互相比未接入，文档已说明 |
| 多裁判全部有效才聚合、保留分歧与失败 | 已实现；目前为平均规则，多数投票不在本轮范围 |
| 请求、原文、版本、用量归档；裁判与被评模型分开统计 | 已实现，测试覆盖 |
| 裁判独立缓存命名空间、同配置裁判标记 | 已实现；不等于真实判断独立性已获验证 |
| 离线重解析；改真实提问时拒绝重放 | 基础路径已实现，纯后处理参数边界存在 J1 |
| 人工标签一致率入口 | 已实现，输入校验存在 J2 |
| 真实校准、公开主观评测集 | 待验证／未接入，不计为最小实现通过的证据 |

## 本机验证

- `UV_CACHE_DIR=/private/tmp/harness-review-uv-cache uv run --no-sync pytest -q -rs`：**272 passed、18 skipped**。
- ruff check、ruff format --check 通过（98 files already formatted）。
- Docker 不可用，minisweagent／swebench 可选依赖相关测试跳过；没有独立复核开发文档中的全部 307 passed。
- 两个下述问题均已实际复现，无需密钥或真实模型。

## J1 [P2] 只改后处理参数，离线重评却要求重新调用裁判

位置：`judge/common.py:129-136`、`judge/rubric.py:133-140`，以及 pairwise 的 prompt_description。

inputs_fingerprint 使用完整 prompt_description；其中混入 disagreement_threshold、normalization、pairwise VALUES 等后处理配置，而不只是实际发给裁判的消息与模型参数。

复现：用测试的 rubric MockModel 生成保存运行，把 RubricJudge.disagreement_threshold 从 0.25 改为 0.5，执行 rescore_run。实际裁判请求不变，结果却为 skipped，说明 inputs changed、needs new judge calls。

阈值只判断裁判分数极差是否算分歧，无需重新回答。这与“聚合／后处理变化可以离线重评”的验收要求不符。

验收：拆分裁判输入身份与解析／归一化／聚合身份。输入身份只覆盖实际提问、模型与生成条件；完整 scorer 指纹仍覆盖所有评分行为。纯分歧阈值、纯聚合规则变化可无模型重评，原文不变，行为指纹和必要的历史可查；修改实际显示的 rubric 分值、提示词或裁判模型仍要求新调用。不能只用没有更新版本号的 monkeypatch 证明版本化重评闭环。

## J2 [P2] 非法校准阈值和未参与比较的非法标签未被拒绝

位置：`judge/calibration.py:49-91`、`97-112`。

threshold 未检查为有限的 [0, 1] 数值；rubric 标签仅在有效判断的 pairs 中验证。裁判失败或找不到对应样本时，标签值可能完全未校验。

复现 A：裁判得分 1/3，独立标签为 1。

```text
threshold=0.5：agreement=0.0
threshold=NaN：agreement=1.0
threshold=2.0：agreement=1.0
```

无效阈值使两侧比较都为 False，得到没有意义的“完全一致”。

复现 B：一个 judge_error 样本提供 label=999，run_agreement 正常返回 invalid_judgements=1、compared=0，没有拒绝非法标签；文档承诺标签不合法时会报错。

验收：统计前统一校验 threshold 和整份标签，不依赖裁判是否成功、是否有对应样本。拒绝 NaN／Infinity／越界／布尔等非法值；未知样本的合法标签仍按既定规则计入 labels_without_sample。覆盖无效裁判、未知样本和非法阈值的回归。

## 下一步

1. 一个小 PR 补 J1、J2；用 MockModel、保存记录和离线校准测试，不需要部署模型。
2. 补修复评后进入阶段八最小适配 Agent：生成候选 adapter diff，用冻结的独立正反例验证，不允许修改答案或官方评分规则。
3. 阶段六真实校准、阶段十真实消融继续在本地／API 队列中验证。功能验收不等于裁判质量已被证明。
4. 校准报告保留无效／不稳定判断的计数和覆盖率，不能只展示筛选后的一致率。

不要求本轮增加完整多裁判投票系统、全部公开主观评测集或大型实验；保留文档明确的小范围与待验证边界。
