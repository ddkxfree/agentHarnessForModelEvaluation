# 46 · Harbor 第 44 轮补修复评

日期：2026-11-03。基准 master `f154ce6`（PR #27 已合并，补修末端 `4e303c0`），开始时工作区干净。本轮只修改评审文档，没有修改实现或发起模型调用。

**结论：H1a／H3a／H4a 的原复现主路径通过，macOS 专项回归通过；仍有 H4b 旧快照脱敏迁移缺口，公开旧归档前需要补修。另发现进度 45 文件没有提交。可以继续本机无模型验收和只读页面开发，不能据此宣称全部真实环境验收已完成或旧归档已适合公开。**

## 已核实的修复

- H1a：用进程组成员判定退出，macOS 原孤儿取消用例及新增 EPERM 用例通过；不能终止的组不会被写成成功取消。
- H3a：IMPORTER_VERSION 已升到 2；旧归档显示警告，相同来源不再误报 unchanged；replace 能重导入，rebuild 能在源 Job 不在时从快照修复分母。专项回归覆盖这两条迁移路径。
- H4a：新导入／日志发布的无效 UTF-8、NUL、UTF-16-LE 和二进制内容都会处理已知秘密；原字节泄露复现及新增用例通过。此结论针对新导入／发布，不包括下述旧快照迁移。

## H4b · P1：rebuild 保留旧版未脱敏的公开快照，却清除版本警告

位置：`src/harness/integrations/harbor/importer.py:598` 的 rebuild，尤其读取快照后直接 build_records、提升 importer 身份的路径。

rebuild 验证原快照哈希，但不重新调用脱敏函数，也不迁移 raw/。版本 1 导入的二进制或无效 UTF-8 文件可能含秘密，正是上一轮 H4a 的影响范围。现在 rebuild 只重算 trials.jsonl 并把记录改成版本 2，旧版本警告随之消失，raw/ 中的敏感字节却不变。哈希一致只能证明没被修改，不能证明已经脱敏。

本轮用真实旧实现 `a907ef3:src/harness/integrations/harbor/importer.py` 在临时目录导入 fixture，加入含 NUL／无效 UTF-8 的 dump.bin 和纯合成凭据；当前环境同时提供该合成凭据供脱敏识别，没有使用真实密钥。结果：

```text
旧快照中包含合成凭据：True
旧归档：有 importer version 1 警告
当前 rebuild 后 importer：version 2 / mapping_version 1
rebuild 后快照仍包含合成凭据：True
rebuild 后 record_warnings：[]
当前版本全新导入同一 Job 后包含合成凭据：False
```

建议补修：明确分开映射版本与快照脱敏状态。若 rebuild 仅负责重算统计，必须保留旧快照风险提示／不可公开状态，并提供独立的重新脱敏迁移路径；也可在受控迁移中产生新版脱敏快照并更新存储哈希、替换记录，保留原始证据的私有边界。不能为保持旧 index 不变而把有风险的旧快照当作已升级。

验收需从真实旧导入器产物开始，覆盖“源 Job 已删除，仅余含敏感字节的旧快照”。迁移后公开产物不含合成秘密，或明确持续标记不能公开；重建统计正确、原始／存储哈希与处理历史可解释。新增回归应同时检查映射结果和 raw/，不能只检查 importer 数字与分母。

## D1 · P2：交接索引指向未提交的第 45 轮记录

roadmap 和 progress 索引都链接 `docs/progress/45-harbor-review44-fixes.md`，但本地和 HEAD 树均无此文件。提交 `4e303c0` 名称提及 progress 45，实际只改 README、progress 索引和 roadmap 三个文件。开发交接中的修复说明／验证证据因此无法打开。

请补交该记录，或将引用改为真实存在的文档；不要由评审者补写开发者的未核实实验过程。

## 验证与范围

在允许 macOS ps 的环境运行带 120 秒看门狗的 Harbor 假进程／离线专项：

```text
.venv/bin/python -m pytest -q tests/test_harbor.py --tb=short
91 passed, 5 skipped in 28.93s
```

五个跳过均为无 Linux /proc 的参数组，不是跳过 macOS 取消用例。ruff check 通过；ruff format --check 为 150 files already formatted。

基础回归在允许 ps 的环境，明确 ignore tests/test_docker_sandbox.py，并逐一 deselect tests/test_onboard.py 中标注 needs_docker 的六项真实容器测试，避免上轮基础命令仍触发镜像准备的问题；其余测试保持原样，通过 subprocess.run(timeout=120) 设置看门狗：**443 passed、9 skipped、6 deselected，57.03 秒**。九个跳过包括五个 /proc 参数组及四个可选依赖用例；六项 deselect 为本轮主动排除，不能计作通过。首次执行申请的自动审批超时、未启动，唯一一次重试后执行完成。

本轮不执行真实 Harbor Oracle／nop、不运行真实 Docker 验收、不调用收费模型，未迁移或修改用户的 runs/。文档 diff 与围栏检查通过；链接检查只发现 D1 已记录的两处第 45 轮缺失链接，其他所查链接存在。

## 下一步

先让 Opus 补 H4b 和 D1，再做针对性复核。随后用户本机继续 Harbor Oracle=1、有效 nop=0 和 Native Docker 回归。网页可以并行做只读展示，但公开导出必须排除 local/ 并处理旧快照迁移状态；旧归档未经重新脱敏前不要整体公开。DeepSeek 的批量实验仍按单题验证后扩大固定小子集的顺序，不因本轮专项通过而自动启动。
