# 24 · 阶段六补修：裁判输入身份与后处理分开、校准输入校验

日期：2026-10-25。对应 [22](22-stage6-review.md) 的 J1、J2。只用 MockModel 和已保存记录测试，没有调用真实模型；**真实裁判校准仍待验证**。

## J1 只改后处理参数，离线重评却要求重新调用裁判

- 原因：`inputs_fingerprint` 用的是完整的 `prompt_description`，其中包含分歧阈值、归一化规则、pairwise 计分值等后处理配置，不只是发给裁判的内容。
- 修复：拆成两个身份。
  - 裁判输入身份 `inputs_fingerprint`：只覆盖实际提问和提问对象，包括模式、系统提示全文、用户消息的组织方式、pairwise 的两种顺序、每个裁判的模型与生成参数（`request_description()`）。rubric 只通过系统提示到达裁判，所以标准名称、说明和分值的变化都会体现在系统提示里。
  - 完整 scorer 指纹 `fingerprint`：仍覆盖全部评分行为，包括提示词、解析器版本、归一化、聚合规则版本、分歧阈值和计分值。
- 版本化的离线重评：
  - rubric 的分歧阈值改为构造参数，也是 `open-qa` 的任务参数 `disagreement_threshold`（默认 0.25，必须是 [0, 1] 内的有限数）。
  - `harness rescore` 新增 `-p task.key=value`，在保存的任务参数上覆盖只影响评分的参数：

    ```bash
    harness rescore runs/<run> -p open-qa.disagreement_threshold=0.5
    ```

  - 覆盖参数后，用新参数重建任务条目。除 `params` 和 `scorer` 外，任何字段与原运行不同（solver、数据集、样本 ID、时限、预算）都直接报错，而且所有任务都在写任何文件之前检查，避免中途失败留下一半改写的运行。
  - 改变裁判或提示（如 `-p open-qa.judges=[...]`）会改变裁判输入身份，仍然跳过，说明需要重新调用裁判。
  - 重评后，manifest 中的任务参数和 scorer 描述更新为新值；任务的 `rescore_history` 记录旧指纹、新指纹和 `params_changed`（旧值与新值）；每个样本的 `rescore_history` 保留之前的分数和聚合详情。裁判原文不变。
  - 代码中的聚合规则变化需要同时更新规则版本号（如 `both_orders_mean_require_all_v1` → `_v2`），指纹和历史因此能看出是哪条规则。

## J2 非法阈值与未参与比较的非法标签未被拒绝

- 原因：阈值没有检查；rubric 标签只在有效判断的样本上才转换和检查，裁判失败或找不到样本的标签完全不检查。
- 修复：统计前统一检查。
  - 阈值必须是 [0, 1] 内的有限数，拒绝 NaN、Infinity、越界值和布尔值。
  - 整份标签逐个检查，不管对应样本是否存在、裁判是否成功：rubric 标签同样要求 [0, 1] 内的有限数，pairwise 标签必须是 `candidate`、`reference`、`tie` 之一。
  - 未知样本的合法标签仍计入 `labels_without_sample`。
- 报告新增 `coverage`：参与一致率计算的样本占有标签样本的比例。无效和不稳定的数量仍单独列出，不能只看筛选后的一致率。
- `harness judge-agreement` 与 `harness rescore` 遇到这类错误时以参数错误退出，给出原因。

## 测试

`tests/test_judge.py` 从 42 个增加到 61 个：

- 后处理不进入裁判输入身份：阈值不同时输入身份相同、完整指纹不同；标准分值、比较标准或裁判模型不同时输入身份不同。
- 分歧阈值越界、NaN、布尔值被拒绝。
- 用两个打分不同的 MockModel 裁判（极差 1/3）生成运行，`rescore -p open-qa.disagreement_threshold=0.5` 不调用模型：
  - 分歧从 true 变为 false，分数和裁判原文不变；
  - 样本历史保留旧的聚合详情；
  - manifest 的参数、scorer 描述、`params_changed`、新旧指纹都正确，裁判输入身份不变；
  - 之后再执行不带参数的 rescore，保持新阈值，没有变化。
- pairwise 聚合规则带版本号变化（`_v2`，顺序不一致计 0 分）后离线重评：分数从 0.5 变为 0，输入身份不变，历史记录旧指纹。
- 用参数改变裁判时仍需要重新调用裁判，manifest 参数不被改写。
- 用参数改变样本（`ids`）或写不存在的任务名时报错，样本文件和 manifest 保持原样。
- 非法阈值 NaN、Infinity、2.0、-0.5、True 被拒绝；同一样本在合法阈值 0.5 下的一致率为 0。
- 裁判失败的样本和不存在的样本上的非法标签（999、字符串、pairwise 的 `A`）被拒绝；合法时计入无效判断与无样本标签，`coverage` 为 0。
- 命令行 `harness rescore -p` 的成功与拒绝。

这些新用例在补修前的代码上有 20 个失败。

## 验证

- 完整环境（含 extra，本机 Docker 实际运行）：`uv run pytest -q` 326 passed。
- 只装基础依赖：301 passed、8 skipped。
- `ruff check`、`ruff format --check` 通过。

## 说明

- PR #19 之前保存的 open-qa 运行，裁判输入身份按旧的方式计算，与新代码不一致，rescore 会跳过它们并说明需要重新调用裁判。这些都是开发时的示例运行，没有真实裁判运行受影响。
- 真实裁判校准、真实消融仍单列待验证。阶段八在单独的分支开发。
