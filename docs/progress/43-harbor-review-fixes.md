# 43 · Harbor 第 42 轮评审补修（H1～H7）

日期：2026-11-03。分支 `cursor/harbor-review-fixes-7316`，基于 master `609efe2`（含 PR #25 与第 42 轮评审记录），未重置已合并提交。**结论：H1～H7 均已在代码中修复并有离线回归测试；真实 Oracle=1／nop=0 与 Native Docker 回归仍待用户在本机执行。**

先更正此前说法：41 之后我曾表示“换机器重跑即可、不需要再改代码”，这是错的。第 42 轮评审找出的 H1～H7 是实现缺陷，不是环境问题；本轮全部改代码修复。

## 修复内容

### H1 进程身份跨平台、不确定时拒绝

- 身份＝pid＋启动时刻＋开机标记＋主机名。Linux 读 `/proc/<pid>/stat`；其他平台（macOS）用 `ps -o lstart=,stat=`（固定 `LC_ALL=C`、`TZ=UTC`），开机标记取 `sysctl kern.boottime`。僵尸进程视为已退出。
- 核对结果改为四态：`none`（从未启动）、`alive`、`gone`（不存在或 PID 已被复用）、`unknown`（未记录启动时刻、换了主机、`ps` 失败）。旧实现在 macOS 上把“无法判断”当成“已退出”。
- `unknown` 时：status 显示 `unknown` 并说明原因；cancel 不发信号、不改状态、保留取消请求，CLI 退出码 1；resume 与首次启动都拒绝。`alive` 时 resume 拒绝，避免两个 Harbor 操作同一 Job。
- 测试对 `proc` 与 `ps` 两种方法各跑一遍：孤儿进程取消、PID 复用不误杀、身份未知时拒绝取消和恢复。

### H2 取消请求带时间戳，在锁内核对

- 取消请求文件写入请求时刻（ns，原子替换）。启动和恢复都在持锁后检查：取消时刻不早于本次请求时刻（首次运行取 prepare 时刻，resume 取用户发出命令的时刻）则不启动，状态记 `cancelled`，说明“cancelled before Harbor started”。
- 更早的取消被本次显式恢复取代：删除请求，并在 state 里记 `superseded_cancel_ns`。首次运行要求状态为 `prepared`，不会再把已取消的运行拉起。
- prepare 过程中到达的取消不会被随后写入的 `prepared` 覆盖。
- resume 在锁内按同样规则把“监督进程已死且 Harbor 已退出”的 running 视为 interrupted，避免只在 status 视图中可恢复、实际却被拒绝。
- 测试：prepare 后取消（Harbor 从未被调用）、别的进程持锁时取消、prepare 期间取消、旧取消被取代而新取消生效、恢复只执行一次。

### H3 缺失 Trial 计入分母

- 计划数取 `result.json` 的 `n_total_trials` 与 `lock.json` 计划 Trial 数中的较大者。没有目录的计划 Trial 按任务路径匹配，生成 outcome 为 `missing` 的占位样本，重复编号唯一。
- `accuracy` 以全部计划样本为分母，另给 `accuracy_of_scored`；新增 `completeness()`（planned／missing／unfinished）。show、`harbor run` 结束输出、report 和 diff 在不完整时都显示警告，说明缺失与未完成按未正确计入。
- 测试：计划 2 个、存在 1 个且正确时，accuracy＝0.5（不再是 1/1），四个出口均有警告。

### H4 日志与原生文件脱敏，原件只留私有目录

- 运行目录分两层：可分享的 `run.json`、`state.json`、`harbor.log`（脱敏）、`raw/`＋`raw_index.json`（脱敏快照，记录原始 sha256、存储 sha256 与替换次数）、`trials.jsonl`、`harbor_run.json`；私有的 `local/`（0700）存 Harbor 原生 Job 目录 `local/job/<name>/`、未脱敏日志 `local/harbor.raw.log`（0600）、进程身份、锁与取消请求。
- 脱敏对象：转交给 Harbor 的环境变量值、凭据名环境变量值，以及已知凭据形式（`sk-…`、`ghp_…`、`hf_…`、AKIA、Bearer、`*_KEY=…`）。`import_job` 不再有 `copy=False` 原地索引路径。已替换的值不重复计数。
- 测试：假 Harbor 把密钥打到 stdout、stderr、Trial 文件和异常文件；`local/` 之外任何文件都不含密钥，索引中替换次数与两种哈希正确，原生文件哈希与 `sha256` 一致；启动即失败的日志同样脱敏。

### H5 阶段时限与单一清理期限

- 新增 `limits.prepare_s`（默认 120，`--print-config` 时限）、`limits.stop_grace_s`（默认 60，SIGTERM 到 SIGKILL）、`limits.cleanup_s`（默认 120，一次清理全部 Docker 调用共享的期限）。
- 每次运行记录 `timing_s`（harbor／stop／cleanup）；清理记录 `budget_s`、`elapsed_s`、迟到资源、`leftover`、`confirmed`。期限用尽时 `leftover` 为 null（未知），`confirmed` 为 false，错误逐条记录。
- 测试：假 Docker 挂起时清理在期限内结束并记为未知；删除失败如实记录、残留可见；迟到容器在第二轮被删除。

### H6／H7 配置

- 不支持的预算只要不是 null 就拒绝，0 与负数也报错。
- `environment.delete=false` 直接拒绝并说明原因（本平台总会删除本 Job 的容器）；argv 恒传 `--delete`，能力表显示“always removed”。

## 检查

```text
uv run pytest -q --ignore=tests/test_docker_sandbox.py
439 passed, 4 skipped

uv run pytest -q tests/test_harbor.py
81 passed

uv run ruff check . && uv run ruff format --check .
All checks passed! / 148 files already formatted
```

另做了变异检查：分别去掉锁内取消核对、启动时刻比较和日志脱敏，相应测试都会失败。

## 未做与待验收

- 开发 VM 是 Linux；`ps` 路径在 Linux 上测试，未在真实 macOS 上运行。请在本机运行 `uv run pytest -q tests/test_harbor.py`，确认两种身份方法的参数化用例在 macOS 上通过（`proc` 组会因无 `/proc` 跳过）。
- 真实 Harbor 验收由用户在本机执行：fix-git Oracle 应得 1、nop 应得 0（需要 compose 网络能出网），以及 Native Docker 回归 `tests/test_docker_sandbox.py`。本轮未运行真实 Harbor、Docker 或任何模型。
- `ps` 的启动时刻精度为秒：同一秒内 PID 被复用仍无法区分，概率极低，记为已知限制。
