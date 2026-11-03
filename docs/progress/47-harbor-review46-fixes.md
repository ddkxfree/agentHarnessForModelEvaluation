# 47 · Harbor 第 46 轮复评补修（H4b／D1）

日期：2026-11-03。分支 `cursor/harbor-review46-fixes-7316`，基于 master `0269d89`（含 PR #27 与第 46 轮复评记录）。**结论：H4b 已修复。用真实旧导入器（`a907ef3`）生成的归档，删除源 Job 后执行 rebuild，公开产物中不再含合成凭据，并保留“分享前需审核”的标记。D1 已补交进度 45。**

## D1 补交进度 45

上一轮写进度 45 时，文件写入与提交是同时发出的，提交时文件还不存在，所以 `4e303c0` 只包含索引、路线图和 README 三处改动。本分支第一个提交 `771dc5a` 补交了原文件，内容未改动。本轮每次提交后都检查 `git status`，确认没有遗漏未跟踪文件。

## H4b 旧快照迁移

原因：rebuild 只重算统计并把 importer 版本改为 2，没有重新脱敏 `raw/`。版本 1 存下的二进制和无效 UTF-8 文件里的秘密原样保留，旧版本警告却随之消失。哈希一致只说明快照没被改动，不能说明已脱敏。

修复：

- **快照的脱敏状态与映射版本分开记录。** 新增 `REDACTION_VERSION = 2`：1 表示只处理 UTF-8 文本，2 表示二进制和无效 UTF-8 也逐字节处理。导入时写入 `raw.redaction = {version, from}`。没有该字段的旧记录，importer 1 视为脱敏版本 1，importer 2 视为脱敏版本 2（第 45 轮起已逐字节脱敏）。
- **警告分开显示。** `record_warnings()` 取代原来的 `stale_warning()`，把统计过期和快照不可分享分成两条：
  - 脱敏版本过旧时显示“do not share it”；
  - 迁移过的快照始终显示“review raw/ before sharing”，因为迁移只能去掉当时已知的值。
  - 两条都会出现在 show、`harbor run` 结束输出、report 和 diff 中。
- **rebuild 按两条路径处理。**
  - **私有原件仍在**（`harness harbor run` 产生、`local/job` 还在）：从原件重新导入，得到新版脱敏快照和原始哈希；保留 run／state 与私有 `job_dir`，记录 `rebuilt.from = "original job"`。
  - **只剩快照**：先用 `verify_snapshot` 核对。若脱敏版本过旧，用当前规则重新脱敏每个文件，可去除的内容包括：
    - 已知凭据形式；
    - 凭据名环境变量；
    - `--secret-env NAME` 指定的值；
    - 收据中转交给 Harbor 的变量。

    索引保留原始 `sha256`，更新 `stored_sha256` 和 `redacted`。新快照和新索引在同一步替换，记录的 `index_sha256` 与累计替换次数也随之更新。`raw.redaction` 记录 `migrated_from`、`migrated_at`、`redacted_at_migration` 和 `known_values`，然后才重算统计。
- **导入冲突说明两种原因。** 不带 replace 重新导入时，若统计版本或脱敏版本过旧，ImportConflict 会同时列出这两类原因，不会返回 unchanged。

### 用真实旧导入器复现

取 `a907ef3` 的 importer.py（IMPORTER_VERSION 1），在临时目录导入 fixture，并加入含 NUL／无效 UTF-8 和合成凭据的 `dump.bin`。合成凭据通过 `SYNTH_API_KEY` 提供，没有使用真实密钥。删除源 Job 后用当前实现 rebuild：

```text
old importer version: 1 | old snapshot contains credential: True
warnings before: [importer version 1 …, snapshot … redaction version 1 … do not share it …]
rebuilt from: snapshot | redaction: {version 2, from older snapshot, migrated_from 1, redacted_at_migration 1, known_values 2}
after rebuild contains credential: False | verify_snapshot: []
warnings after: [snapshot raw/ was redacted again from a version-1 snapshot … review raw/ before sharing]
```

### 回归测试

单元测试用版本 1 的规则模拟旧导入（非文本不脱敏，记录无 redaction 字段），覆盖：

- 旧快照显示“不可分享”警告；
- 删除源 Job 后执行 `harbor rebuild --secret-env`：
  - 公开文件不含凭据，`dump.bin` 只有凭据处被替换；
  - 原始哈希不变，存储哈希与 `index_sha256` 更新，`verify_snapshot` 通过，统计正确；
  - 保留审核标记，没有残留临时目录；
- 迁移时不知道凭据值：如实记 0 次替换，审核标记保留；
- 脱敏版本过旧时，重新导入报冲突，replace 后不再含凭据；
- harness 运行的归档从 `local/job` 原件重建，`local/` 之外不含凭据，状态保留，没有警告。

变异检查：跳过快照重新脱敏后，2 个用例失败。

## 检查

```text
uv run pytest -q --ignore=tests/test_docker_sandbox.py
459 passed, 4 skipped

uv run pytest -q tests/test_harbor.py
101 passed

uv run ruff check . && uv run ruff format --check .
All checks passed! / 152 files already formatted
```

开发 VM 未启动 Docker，onboarding 的 Docker 用例在此跳过。没有调用模型，没有运行真实 Harbor，也没有修改用户的 `runs/`。

## 限制与待用户执行

- 只剩快照的迁移只能去掉已知形式和当时可获得的值。其他编码中的秘密，或迁移时不在环境中的值，仍可能留在 `raw/`，因此迁移后的快照始终带“分享前审核”标记，不能视为已可公开。
- 已有 Harbor 归档请运行 `harness harbor rebuild runs/harbor/<run>`。若当时转交过密钥，加上 `--secret-env <变量名>`。公开导出须排除 `local/`，迁移后的 `raw/` 需人工审核。
- 真实 Harbor Oracle=1、有效 nop=0 和 Native Docker 回归仍待本机执行。
