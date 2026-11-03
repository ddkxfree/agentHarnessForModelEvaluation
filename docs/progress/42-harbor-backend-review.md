# 42 · Harbor 双入口独立评审：仍需补修

日期：2026-11-03。基准为本地 master `c3540ec`，已包含 PR #25 合并提交及分支末端 `6ce06bb`，评审开始时工作区干净。**结论：入口和模块主体已实现，但不能认定仅剩网络验收、不需要改代码。发现 H1～H7，建议在新补修分支修复后复评，再补真实 Oracle／nop 验收。**本轮只更新评审文档，不修改实现。

## 核验范围与结果

阅读开发指南、进度 41、全部 Harbor 集成模块、共享结果视图、CLI 分流与新增测试。没有安装 Harbor，没有运行收费模型、真实 Harbor Job 或新增镜像下载。使用现有假 Harbor／假 Docker 与脱敏 fixtures，在临时目录复现问题，不修改原始运行目录；自建子进程已终止。

```text
.venv/bin/python -m pytest -q --ignore=tests/test_docker_sandbox.py
2 failed, 409 passed, 10 skipped in 42.51s

.venv/bin/ruff check .
All checks passed!

.venv/bin/ruff format --check .
147 files already formatted
```

两项失败为 `test_status_and_cancel_of_an_orphaned_harbor_process`、`test_a_reused_pid_is_never_signalled`，均在 macOS 上因 `process_identity()` 返回 None，测试辅助代码写入 `ident['pgid']` 时 TypeError。不能沿用开发 VM 的测试数字作为本机验收结果。真实 Docker 回归本轮未执行，原网络阻碍的根因没有独立实测确认。

## H1 · P1：进程身份仅支持 Linux，本机孤儿进程不能正确取消

位置：`src/harness/integrations/harbor/process.py:103` 的 `process_identity()` 及 `status()`／`cancel()` 调用链。

实现只读 `/proc/<pid>/stat`、cmdline 与 boot_id；macOS 没有这些文件。真实运行回退只记录 pid／pgid，`is_same_process()` 永远返回 False。监督进程退出后，仍存活的 Harbor 被报告为 interrupted；cancel 跳过发信号却把状态写成 cancelled。此时 resume 也可能被放行，与原进程同时操作一个 Job。

本机定向复现：启动本轮独占的休眠子进程，将其按 supervise 的回退方式写入 process.json，state 置 running；status 得到 `interrupted, harbor_process_alive=False`，cancel 返回 `cancelled`，而 `proc.poll() is None`。随后主动 kill／wait 清理该测试子进程。

补修验收：实现适用于 macOS 和 Linux、可防 PID 复用的身份检测；若无法核实身份，应呈现未知并拒绝危险恢复，不能当成进程已退出。两平台覆盖孤儿取消、PID 复用和活跃 Job 不重复恢复；不能只跳过本机两项失败。

## H2 · P1：启动交接会清除已生效的取消请求

位置：`src/harness/integrations/harbor/process.py:370`，supervise 获取锁后无条件删除 CANCEL，再启动 Popen。

prepare 已创建可定位的运行目录，但 supervise 尚未获得监督锁时，另一 CLI 可以 cancel。cancel 写入取消请求、取得锁并返回 cancelled；随后原 run 进入 supervise，删除取消标记且不检查终态，仍启动 Job。resume 的状态预检在监督锁外，也应覆盖同类交接。

确定性复现，不依赖线程概率：假 CLI 下依次调用 `prepare → cancel(wait_s=0) → supervise`。取消先返回 cancelled，但随后 Job 被创建，实际 run 调用计数为 1，最终状态变 completed。

补修验收：锁内核对启动状态和持久取消请求，首次启动不能复活已取消的运行；显式 resume 对旧取消的清除与本次新取消须有清楚的代次和原子交接。覆盖 prepare 后取消、等待监督锁时取消及 resume 交接，不允许取消已确认后发起新的执行。

## H3 · P1：缺失 Trial 被排除在共享结果统计之外

位置：`src/harness/integrations/harbor/importer.py:310` 和 `src/harness/results/__init__.py:61`。

importer 只为发现的目录生成 trials，虽然单独保存 n_missing，共享读取层没有使用它，show／diff／report 只看 samples。计划内尚未创建目录、或结果目录丢失的 Trial 从样本分母消失。导入命令能看到缺失数量，不等于后续分析正确。

复现：复制 oracle-verifier-offline fixture，lock 中增加一个计划 Trial，Job 总数设 2（1 已完成、1 pending），现有 Trial 的 reward 设 1。导入收据为 `n_total_trials=2, n_trials_found=1, n_missing=1`，但 `open_run().accuracy()` 返回 `n=1, n_correct=1, accuracy=1.0`，共享展示成为 1/1。

补修验收：共享视图保留计划总数、缺失／未启动身份或显式不可解析的数量，完整率与完成样本分数分别展示。不能把不完整 Job 的已观察成功率当整个 Job 成绩；show／diff／report 必须持续提示缺失，不能依赖用户回看 import 输出。

## H4 · P1：子进程输出原样写入归档，已知凭据可泄露

位置：`src/harness/integrations/harbor/process.py:372` 的日志写入与 `importer.py` 的 copy=False 原地索引路径。

Harbor 的 stdout／stderr 直接写 harbor.log，没有对已转交的秘密做脱敏。copy=False 仅脱敏映射用的内存数据，原生 Job 文件仍原样保留。env 传递避免 argv 泄露，但不保证 Agent、工具或异常日志不输出秘密。

使用纯合成的 SOME_KEY（没有读取或使用真实密钥），让假 Harbor 输出其值；supervise 完成后在归档 harbor.log 中仍能找到完整值。现有“不泄密”用例的假程序没有向日志输出密钥，因此没有覆盖此路径。

补修验收：至少对已传入的秘密和现有可识别凭据形式，在持久日志和可分享原生产物归档路径落实脱敏／隔离策略；保留证据处理标记与正确哈希口径。覆盖 stdout、stderr、异常与原生文件，不能仅清理 JSON 映射。敏感原文若必须临时保留，需有独立私有边界且不作为可分享归档。

## H5 · P2：清理没有整体截止时间，取消可能再等待数分钟

位置：`src/harness/integrations/harbor/process.py:187`、`:193`、`:434`。

wall_clock_s 仅控制监督循环。退出后执行两次 cleanup_owned 和一次 leftover_owned，每条 Docker 命令重新获得 60 秒；没有共享清理预算。静态路径核对：即使没有进入逐容器删除，只要 daemon 无响应，五次查询也可各等待 60 秒，另有宽限与 settle。多个容器逐个删除时还会继续累加。cancel 的 wait_s 只限制等待监督者，不限制其后自行清理。

补修验收：对准备、执行、宽限和清理给出明确预算与实际分段耗时；清理使用单一截止时间，各 Docker 调用只获剩余时间。到限记清理失败／未知和未确认资源，不追加完整一分钟。用假 Docker 覆盖持续无响应、多个删除失败与迟到资源，不能为复现让真实环境等待数分钟。

## H6 · P2：不支持的零预算被静默接受

位置：`src/harness/integrations/harbor/config.py:104`。

不支持限制的校验使用真值，而非 `is not None`。本机已验证 `Limits(token_limit=0)`、`Limits(cost_limit_usd=0)`、`Limits(max_steps=0)` 均通过；build_argv 不传这些限制，实际运行无限制于这些字段。尤其费用 0 不应被解释为“不设限制”。

补修验收：任何非 None 的不支持预算均在启动前拒绝，或明确校验为非法值；补零值与负值覆盖，不能用 falsey 判断跳过用户明确配置。

## H7 · P2：公开的 delete=false 配置最终仍删除环境

位置：`src/harness/integrations/harbor/config.py:113` 与 `process.py:434`。

配置允许 environment.delete=false，并向 Harbor 传 --no-delete；但 supervise 无论正常结束还是异常退出都无条件 cleanup_owned，取消分支也同样删除。因此用户要求保留调试环境，最终还是被本平台删除。这项为代码路径确认，未为此创建真实容器。

补修验收：首版若坚持总是清理，直接拒绝 delete=false 并纠正文档；若支持保留，明确正常完成与取消／超时的保留政策，在收据和最终输出显示实际政策，并用假 Docker 验证对应分支。

## 后续执行顺序

1. PR #25 已合入 master，与交接所说的“草稿待合并”不同。不要重置已合入提交；从当前 master 新建补修分支，处理 H1～H7，增加有行为依据的回归。
2. 先用无网络测试复评这些问题。不得将 H1 归为 Docker 网络问题，H2／H3／H4 的确定性复现也不需要真实 Harbor。
3. 补修后再在用户本机执行 Harbor fix-git Oracle=1、nop=0 且验证器实际正常的对照，补完 Native Docker 回归；仍不调用收费模型。
4. 真实模型实验继续由用户配置密钥并自行启动。Oracle／nop 真实验收通过前，不据此作模型或 Agent 优劣结论。

开发记录 41 对失败 nop 无信息量的说明是准确的；这种环境限制可以暂缓验收。但“换机器重跑即可、不需要再改代码”不符合本轮复评证据。本轮未重新运行开发 VM，不能独立确认其网络诊断与清理结果。
