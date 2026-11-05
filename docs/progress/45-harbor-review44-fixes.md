# 45 · Harbor 第 44 轮复评补修（H1a／H3a／H4a）

日期：2026-11-03。分支 `cursor/harbor-review44-fixes-7316`，基于 master `3b77f5e`（含 PR #26 与第 44 轮复评记录）。**结论：三项残留均已修复，按复评给出的复现方式写成回归测试；真实 Oracle／nop 与 Native Docker 验收仍待用户本机执行。**

## H1a 孤儿取消遇到 EPERM

原因：没有 Popen 对象时，SIGTERM 之后用 `killpg(pgid, 0)` 判断进程组是否还在，只捕获 ProcessLookupError。macOS 上，组内只剩未回收的僵尸时探测返回 EPERM，于是 cancel 抛异常，清理、状态与发布都没有执行。

修复：

- 新增 `group_state(pgid)`，结果为 alive／gone／unknown。信号探测只作参考，由 `ps -A -o pid=,pgid=,stat=` 列出的非僵尸成员决定：有成员为 alive，没有为 gone。EPERM 且拿不到成员列表时为 unknown，不当作已退出。Linux 上组内只剩僵尸时信号探测也会成功，现在同样判为 gone，不再空等整个宽限期。
- `_stop_group` 对 ProcessLookupError 和 PermissionError 都不再抛出：SIGTERM、宽限、SIGKILL，再在 `KILL_WAIT_S`（5 秒）内等进程组消失。
- cancel 停止后再核对一次进程组：
  - 已退出：照常在单一期限内清理本 Job 资源，状态记 cancelled 并发布。
  - 仍存活或未知（例如无权向其他用户的进程发信号）：不写 cancelled，在 state 里记录 `cancel: {how, group, at}`，保留取消请求，不删除仍由 Harbor 持有的容器；CLI 返回 `failed: ...`，退出码 1。
- 测试：
  - 僵尸、存活、无成员列表三种情况下 `group_state` 的结果；
  - 模拟 macOS“探测返回 EPERM”时，孤儿取消（proc／ps 两种身份方法）能完成清理并记为 cancelled；
  - 所有信号都被拒绝时，状态仍为 running，进程未被误报为已停止，也没有调用 Docker。

## H3a 旧归档以 unchanged 掩盖错误统计

原因：第 43 轮改变了缺失 Trial 的计数，但 `IMPORTER_VERSION` 仍是 1。重导入时快照索引和版本都相同，直接返回 unchanged，`replace=True` 也不会更新。

修复：

- `IMPORTER_VERSION` 升为 2。版本不同时，不带 replace 的导入报 ImportConflict，说明记录由哪个版本生成、可能有误，以及两条重建路径；带 replace 时从 Job 目录重建，返回 replaced。只有快照和版本都相同才返回 unchanged。
- 新增 `harness harbor rebuild <run_dir>`，用于原始 Job 目录已不在的情况：先用 `verify_snapshot` 核对脱敏快照，被改动则拒绝；通过后用当前版本从快照重建 `trials.jsonl` 与 `harbor_run.json`，记录 `rebuilt.previous_importer`。快照和索引保持不变。
- 读取旧版本生成的归档时，`RunView.warnings()` 给出“records built by importer version N”警告。show、`harbor run` 结束输出、report 和 diff 统一显示 `warnings()`，其中也包括不完整运行的警告。
- 测试：构造版本 1 的归档（计划 2 个、仅记录 1 个且正确，accuracy 1.0），检查警告和 ImportConflict；replace 后为 2 个、0.5，再导入为 unchanged；删除 Job 目录后 rebuild 同样得到 0.5 且索引不变；篡改快照后 rebuild 被拒绝。

## H4a 非 UTF-8／含 NUL 的数据绕过脱敏

原因：`redact_bytes` 遇到 NUL 或 UTF-8 解码失败时原样返回。公开日志和导入快照都走这个函数。

修复：

- 无法作为 UTF-8 文本处理的数据（解码失败、含 NUL、二进制）改为逐字节脱敏：按 Latin-1 一一映射字节后，替换秘密值的 UTF-8 字节，并以 ASCII 模式匹配已知凭据形式，避免非 ASCII 字节被当作单词字符使 `\b` 失效。另外替换秘密值的 UTF-16LE 形式。其余字节原样保留。
- 合法 UTF-8 文本和 JSON 的处理不变。
- 测试：
  - 无效 UTF-8 出现在开头和中间、NUL、UTF-16、紧邻非 ASCII 字节的 `sk-`／`API_TOKEN=` 都会被替换；非秘密字节不变，没有秘密时返回原字节且计数为 0；
  - 按复评复现方式调用 `_publish_log`，公开 `harbor.log` 不含标记，哈希与计数正确；
  - 导入含二进制文件的 Job，快照中不含标记，索引的原始哈希与原件一致，`verify_snapshot` 通过。

已知限制：其他编码（如 UTF-16BE、压缩或 base64 内容）中的秘密不能识别。这类文件仍会进入脱敏快照，可分享前应人工审核。

## 检查

```text
uv run pytest -q --ignore=tests/test_docker_sandbox.py
454 passed, 4 skipped

uv run pytest -q tests/test_harbor.py
96 passed

uv run ruff check . && uv run ruff format --check .
All checks passed! / 150 files already formatted
```

变异检查：

| 去掉的修复 | 失败的用例 |
|---|---|
| EPERM 判定 | 4 |
| 版本号退回 1 | 2 |
| 字节级脱敏 | 8 |

开发 VM 未启动 Docker，`tests/test_onboard.py` 的 Docker 用例在此跳过。复评中提到的 `test_docker_blocks_adapter_writes_outside_its_scratch_directory`（期望 import_error，实际 env_error）本轮未复现、未修改。

## 待用户本机执行

1. 在 macOS 上运行 `uv run pytest -q tests/test_harbor.py`，确认 H1a 用例与 `test_status_and_cancel_of_an_orphaned_harbor_process[ps]` 通过。
2. 已有 Harbor 归档运行 `harness harbor rebuild runs/harbor/<run>`，或带 `--replace` 重新导入。
3. 真实 fix-git Oracle=1、有效 nop=0，以及 Native Docker 回归。
