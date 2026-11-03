# 41 · Harbor 双执行入口第一批实现（G0～G6）

日期：2026-11-03。分支 `codex/harbor-backend`（基于 master `766674f`），按 [Harbor 集成指南](../harbor-integration-plan.md) 实现。**状态：工程集成完成；真实无模型链路中 run／import／取消／恢复通过，fix-git Oracle 得满分未验收（本机 Docker 网络阻碍，见下），真实模型实验待用户执行。**待 Codex 评审，未合并。

## 做了什么

保留 `harness run` 等 Native 入口不变，新增 `harness harbor` 命令组：

| 命令 | 作用 |
|---|---|
| `harness harbor doctor [-c FILE]` | 检查 Harbor 版本、Docker daemon／compose 插件、配置、任务指纹和 Agent 所需环境变量名；打印能力表；不运行任何东西、不输出凭据值 |
| `harness harbor run -c FILE` | 前台运行：先 `--print-config` 记录解析后的 JobConfig，再在独立进程组中运行 Harbor，执行总时限，结束后清理本 Job 容器并导入 |
| `harness harbor import JOB_DIR` | 离线导入任意 Harbor 0.25.0 Job 目录（无需 Harbor／Docker／key），只读源目录 |
| `harness harbor status／cancel／resume RUN_DIR` | 跨进程读取状态；取消只作用于可证明归属的进程和容器；恢复用 Harbor 原生 `harbor job resume` |
| `harness show／diff／report` | 识别 `harbor_run.json` 后走统一只读视图；Native 结果路径不变；两种来源可混合 diff |

`harness compare` 未改动，不能驱动 Harbor。

模块：`src/harness/harbor_cli.py`（薄命令层）；`src/harness/integrations/harbor/` 下 `runtime.py`（安装身份）、`config.py`（配置与能力）、`importer.py`（快照与导入）、`mapping.py`（Trial → 共享字段）、`process.py`（执行、状态、取消、恢复）；`src/harness/results/`（RunView／SampleView 与 diff／report）。`core/`、Solver、DockerProvider 不导入任何 Harbor 代码；项目环境不安装 `harbor` 包。

## G0 固定版本与预检

- 选定 PyPI 正式版 **harbor 0.25.0**（wheel sha256 `51923ac567fc57b03279e93468b105f689628bcac2cd3d541d4e44aa5c0ec309`，作者与上游仓库一致）。
- 安装在独立 venv（默认 `~/.local/share/agent-harness/harbor-0.25.0`，Python 3.12），用带哈希的通用锁文件 `configs/harbor/requirements-0.25.0.lock`（sha256 `caa18c10…f965`）：
  ```bash
  uv venv --python 3.12 ~/.local/share/agent-harness/harbor-0.25.0
  VIRTUAL_ENV=~/.local/share/agent-harness/harbor-0.25.0 uv pip install --require-hashes -r configs/harbor/requirements-0.25.0.lock
  ```
- 可执行文件按 配置 → `HARNESS_HARBOR_BIN` → 默认安装 的顺序确定，**不从 PATH 查找**；`--version` 不是 0.25.0 即拒绝。
- 配置 schema 只包含已对照 0.25.0 CLI 核实的选项（`--path --agent --model --agent-kwarg --n-attempts --n-concurrent --max-retries --timeout-multiplier --env docker --delete/--no-delete --extra-docker-compose --jobs-dir --job-name --yes --quiet`），不透传任意参数；未知字段报错。
- token／费用／步数限制 0.25.0 CLI 无法执行，配置了即启动前报错；Agent kwargs 名称像凭据（key／token／secret／password）时拒绝，密钥只能通过 `agent.env` 写变量名，由进程环境传给 Harbor。
- 示例配置：`configs/harbor/fix-git-oracle.yaml`、`fix-git-nop.yaml`、`fix-git-terminus2.yaml`（真实模型示例，**未运行**）。

## G1 离线导入

- 版本门：Job `lock.json` 的 `harbor.version` 必须为 0.25.0、`schema_version` 必须为 3，否则拒绝并不写任何输出。
- 快照：遍历不跟随符号链接；符号链接、FIFO 等特殊文件、超过 64 MB 的文件只记录不复制；每个文件记大小与 sha256，脱敏后另记存储哈希；读完再扫描并比对，源目录在导入中变化则不生成快照。`verify_snapshot` 可检出快照被改或缺失。
- 脱敏：JSON 中凭据类键（不含 `*_name` 这类变量名）、`sk-`／`ghp_`／`hf_`／AKIA／Bearer 形式和 `XXX_API_KEY=...` 赋值，以及当前环境中名为 key／token／secret／password 的变量值。
- 运行 ID 由 Job 名和 `lock.json` 创建时间决定；同内容同导入器版本重复导入为 no-op，内容或导入器变化需 `--replace` 重建，不会重复计数。
- 映射规则：只有单一 `reward` 且为 0／1 时形成 correct／wrong；多个 reward 或部分分保留原值并标“未聚合”（unscored）；异常保留原始类型、阶段，已知类名映射类别，其余为 `unknown`；oracle／nop 用量为 `not_applicable`，模型 Agent 未报告用量为 `unknown`，都保持 null，不填 0；缺失 Trial、运行中 Trial 显式计数。
- 0.25.0 源码核实：Harbor 重试会删除被重试的 Trial 目录，只在 Job 统计中记 `n_retries`；`source_trial` 表示 regrade。因此独立重复按任务＋任务校验和＋Agent／模型分组、按开始时间编号，重试次数只在 Job 级展示。
- 测试样例 `tests/fixtures/harbor/0.25.0/` 来自本机 Harbor 0.25.0 真实运行，只改写本地路径：环境启动失败（compose 插件缺失，退出码 0 但 Trial 异常）和验证器无法联网导致的 reward 0。

## G2 统一只读分析

`open_run()` 按目录内容识别来源：Native 通过 SampleResult 适配，Harbor 读映射后的 Trial。diff 按样本＋重复配对，任务指纹（Native）或 Harbor 校验和不同则不配对，无法核实时提示；跨后端对比明确提示“系统差异，不是引擎差异”；用量覆盖不完整时成本不可比；总计只在全部模型样本都报告时给出。输出到终端和 Markdown 前去掉终端控制序列。

## G3／G4 执行、状态、取消与恢复

- 运行目录：`run.json`（可分享收据：请求配置、解析后 JobConfig（脱敏）、argv、Harbor 身份、任务指纹、能力表、环境变量名）、`state.json`、`harbor.log`、`job/<name>/`（Harbor 原生目录，原地索引不复制）、`local/`（0700，仅本机：进程身份、锁、取消请求）。（第 43 轮已改：原生目录移入 `local/job/`，分享内容一律脱敏，见 [43](43-harbor-review-fixes.md)。）
- 进程身份为 pid＋/proc 启动时刻＋boot id＋主机名；cancel／status 先核对，PID 被复用时不发信号。`flock` 互斥：同一运行同时只能有一个监督进程，resume 不会并发执行；监督进程崩溃锁自动释放，status 显示 `interrupted` 或 `orphaned`（Harbor 仍在跑）。
- 停止：对 Harbor 进程组发 SIGTERM（0.25.0 转为 KeyboardInterrupt 并取消 Trial），宽限 60 秒后 SIGKILL；随后删除本 Job 自己的容器和网络（compose 项目名以本 Job Trial 目录名开头，即 `<trial>__env`、`<trial>__verifier__*`），5 秒后再查一次迟到资源并记录残留，失败如实写入 state。
- 退出码只记录不决定成败：Job 完成但 Trial 失败为 completed＋wrong；非零退出仍导入已有 Trial。
- 单元测试用假 Harbor／假 docker 覆盖：启动失败、部分完成后非零退出、超时、忽略 SIGTERM 需 SIGKILL、另一进程取消、孤儿进程取消、PID 复用不误杀、恢复互斥与不重复计数、密钥不进入 argv／文件。

## G5 真实无模型验收（本机，Docker 29.1.3，compose 2.40.3）

| 检查 | 结果 |
|---|---|
| `harness harbor doctor -c configs/harbor/fix-git-oracle.yaml` | 通过：Harbor 0.25.0、Docker、compose、任务指纹 `1b12832fdb57503e` |
| 任务镜像 | `alexgshaw/fix-git:20251031` 本机 RepoDigest 与项目 pin `sha256:61e431c0…0119` 一致（Harbor 按 tag 使用） |
| nop 对照 `harness harbor run -c configs/harbor/fix-git-nop.yaml` | Harbor 退出 0，reward 0.0，原生 `reward.txt`＝0 与导入值一致，无残留容器。**但验证器自身因无网装不上 curl／uv，该对照不能证明任务能区分 nop**，记为“链路通过、对照无信息量” |
| 真实取消（Oracle Job，容器启动后 3 秒取消） | 监督进程收到取消，Harbor 退出 130，Trial 记为 cancelled；本 Job 容器与网络无残留；同时运行的无关哨兵容器保持 running（之后删除） |
| 真实恢复 `harness harbor resume` | `harbor job resume` 退出 0（152 秒），被取消的 Trial 由 Harbor 移除后重跑；导入后仅 1 个 Trial，无重复；无残留 |
| fix-git Oracle 得 1 分 | **未验收（blocked）**：Oracle 解完成，但验证器在 compose 自定义网络中无法访问 deb.debian.org，reward 0。默认 bridge 可联网；本机 legacy iptables NAT 只为 172.17/16 做 MASQUERADE。指南不允许改宿主防火墙；同一故障已用完首次＋两次修复尝试，停止该依赖链 |

运行与日志在被忽略的 `runs/harbor/`、`runs/harbor-dev-session/`（账本 `progress.json`），未提交。

## 检查

- 定向：`tests/test_harbor.py` 59 passed（无网络、无 Harbor、无 Docker）。
- 基础依赖环境（未安装 Harbor、未装可选依赖）`uv run pytest -q --ignore tests/test_docker_sandbox.py`：416 passed、4 skipped（minisweagent／swebench 可选依赖缺失）；`ruff check`、`ruff format --check` 通过。
- Native Docker 回归 `tests/test_docker_sandbox.py`（限时 900 秒）：前 7 项通过，含三道自建终端任务 Oracle 全过、不操作全失败、沙箱隔离／超时清理；1 项跳过（mini-swe-agent 未安装）；`test_terminal_bench_oracle_passes_and_doing_nothing_fails[oracle]` 的验证器在默认 bridge 上 `apt-get update` 卡住，整体超时（exit 124），其后 5 项未运行。超时遗留的 1 个本次测试沙箱容器已按 ID 删除。

## 未完成与后续

1. 在能让 compose 自定义网络出网的 Docker 环境（例如用户本机 Docker Desktop）运行 `harness harbor run -c configs/harbor/fix-git-oracle.yaml`，期望 reward 1；再跑 nop 对照，期望 0 且验证器日志正常。
2. 真实模型示例 `fix-git-terminus2.yaml` 由用户在配置密钥后自行运行；token／费用限制目前不支持。
3. 多任务数据集、矩阵调度、ATIF 深度解析、regrade 未做（见指南第 10 节）。
