# 09 · 接入真实 Terminal-Bench（第五阶段加固，第四轮）

日期：2026-10-24。在 08（独立复核）的基础上开发。

## 目标

05 复评列出的第四轮验收项：

1. 接入真实的 Terminal-Bench（固定版本，3～5 题）；
2. Oracle 全部通过，不做任何操作的 Agent 全部失败；
3. 不改 `core/`，验证平台能容纳第二种 Agent 评测集。

## 选用的版本与题目

- 来源：[laude-institute/terminal-bench-2](https://github.com/laude-institute/terminal-bench-2)，固定在提交 `2fd12b88aafdd04a52c298e3940bcb189f9766d6`。这是 Terminal-Bench 2.0，使用 Harbor 任务格式（`instruction.md`、`task.toml`、`environment/Dockerfile`、`tests/test.sh`、`solution/solve.sh`）。
- 选了 5 题，标准是镜像小、判分快，并覆盖不同类别：

| 题目 | 类别 | 难度 |
|---|---|---|
| `fix-git` | 软件工程（git 操作） | easy |
| `log-summary-date-ranges` | 数据处理（日志统计） | medium |
| `regex-log` | 数据处理（正则） | medium |
| `openssl-selfsigned-cert` | 安全（证书生成） | medium |
| `sqlite-db-truncate` | 调试（二进制数据恢复） | medium |

- 每题双重固定：
  - **任务文件**：按目录内容指纹固定（`fingerprint_dir`，与自建终端任务相同的算法）。
  - **镜像**：使用官方预构建镜像，按 registry digest 固定，例如 `alexgshaw/fix-git@sha256:61e4...`。不在本地构建，因为 Dockerfile 里有 `apt-get install` 等步骤，本地构建的结果会随时间变化。
- Terminal-Bench 要求不要把题目再分发进训练语料，所以不把题目文件提交进本仓库。首次使用时从 GitHub 按固定提交下载到 `~/.cache/harness/terminal-bench-2/<commit>/`（可用 `HARNESS_CACHE_DIR` 改位置），下载后校验指纹，不一致直接报错并提示删除重下。

## 这一轮做了什么

新增 `src/harness/tasks/terminal_bench.py`，注册为 `terminal-bench`。`core/` 没有任何改动；`solvers/basic_agent.py` 有一处小改动（见下文）。

### 1. 按 Harbor 的流程运行

对照 Harbor 源码（`verifier/verifier.py`、`agents/oracle.py`）实现：

- **Agent 阶段**：从镜像的 `WORKDIR` 开始（解析 Dockerfile 得到），题面是 `instruction.md` 原文。CPU、内存、是否联网取自 `task.toml` 的 `[environment]`。
- **判分阶段**（`HarborVerifierScorer`）：
  1. 创建 `/logs/verifier`，删除可能残留的 reward 文件，防止 Agent 自己写一个 `reward.txt`；
  2. 上传 `tests/` 到 `/tests`；
  3. 运行 `/tests/test.sh > /logs/verifier/test-stdout.txt 2>&1`，超时取 `task.toml` 的 `verifier.timeout_sec`；
  4. 读取分数：`reward.json`（键 `reward`）优先，其次 `reward.txt`；必须是有限数值。
- **Oracle**（`HarborOracleSolver`）：上传 `solution/` 到 `/solution`，运行 `solve.sh`，输出保存为 `oracle.txt` artifact。
- 判分输出完整保存为 `test-stdout.txt` artifact（带 SHA-256），`score.details` 只留尾部 2000 字符。

### 2. 评分失败与答错分开

- 没有 reward 文件、reward 不是数值、reward 为 NaN／inf，都抛异常，记为 `scorer_error`，不当成 0 分。这是 Harbor 的规则（`RewardFileNotFoundError`、`RewardFileEmptyError`），也和 06 修复 NaN 漏判的原则一致。
- 镜像拉取、目录创建、上传失败记为 `env_error`。

### 3. 三种 Solver

`-p terminal-bench.solver=agent|mini|oracle`：

- `agent`：基线 `BasicAgent`。原来提示词里写死了"你在 `/app` 中"，这里的题目工作目录各不相同，所以新增 `workdir=None`，提示词改为"命令从当前目录开始执行"。其他任务的提示词不变，指纹不变。
- `mini`：mini-swe-agent，`cwd=""`，即使用容器的工作目录。
- `oracle`：见上文。

### 4. 与 Harbor 的偏差

偏差写在 `DEVIATIONS` 中，进入评分器的 `describe()`，因此会出现在 manifest 里，改动后 `harness diff` 能发现：

- 命令用 `bash -lc` 执行，Harbor 用 `bash -c`；
- `/logs/agent`、`/logs/verifier` 在判分前于容器内创建，Harbor 是整个 trial 期间从宿主机挂载；
- Agent 是本平台的 Solver，不是 Harbor 的 Agent；
- `task.toml` 中的 `storage_mb`、`gpus` 没有执行；
- 判分脚本超时记为 0 分，Harbor 记为该 trial 出错。

## 用法

```bash
# 校验题目与判分，不需要模型（应为 5/5）
uv run harness run -m none/none -t terminal-bench -p terminal-bench.solver=oracle
# 只跑部分题目
uv run harness run -m ollama/qwen2.5:3b-instruct -t terminal-bench -p 'terminal-bench.ids=[fix-git, regex-log]'
```

## 验证

`uv run pytest -q`：191 passed（本机 Docker 29.1.3 实际运行全部 Docker 集成测试）。`ruff check`、`ruff format --check` 通过。

| 测试 | 验收项 |
|---|---|
| `test_terminal_bench.py::test_reward_files_follow_harbor`、`test_missing_or_invalid_rewards_are_rejected` | 2：reward.json 优先；缺失、非数值、NaN、inf、布尔值都拒绝 |
| `test_terminal_bench.py::test_loader_reads_harbor_metadata` | 1：读取题面、`WORKDIR`、资源限制、超时 |
| `test_terminal_bench.py::test_downloaded_tasks_must_match_the_pinned_fingerprint` | 1：下载内容被改动时报错，不会静默使用 |
| `test_terminal_bench.py::test_verifier_uploads_tests_and_reads_the_reward`、`test_no_reward_file_is_a_verifier_failure` | 2：判分流程；没有 reward 文件是 `scorer_error` |
| `test_terminal_bench.py::test_task_is_registered_with_pinned_revision` | 1：manifest 中的数据集版本 |
| `test_terminal_bench.py::test_agent_prompt_does_not_claim_a_fixed_workdir` | Agent 提示词不再写死 `/app` |
| `test_docker_sandbox.py::test_terminal_bench_oracle_passes_and_doing_nothing_fails` | 2：真实容器中 Oracle 5/5 通过，不操作的 Agent 5/5 得 `reward=0`；镜像 digest 与 artifact 都有记录 |
| `test_docker_sandbox.py::test_terminal_bench_ignores_a_reward_file_planted_by_the_agent` | 2：Agent 预先写入 `reward.txt = 1`，判分前被删除，仍得 0 分 |

命令行实测：`harness run -m none/none -t terminal-bench -p terminal-bench.solver=oracle`，5/5 正确，镜像已缓存时用时约 17 秒。manifest 中记录了仓库、提交、题目列表、偏差列表和 Docker daemon 信息。

## 仍然存在的问题（如实记录）

- 首次运行需要访问 GitHub（下载题目）和 Docker Hub（拉取镜像，5 个镜像共约 1 GB）。Docker 集成测试在无 Docker 或无法下载时跳过。
- 下载用的是 GitHub API 的目录树接口，未登录时每小时限 60 次请求；每题下载需要 1 次，加上文件数次 raw 下载（raw 不计入此限额）。
- 终端任务的判分依赖 Agent 留下的整个容器，没有可单独保存的提交物，`harness verify` 跳过（与 Terminal-Bench 官方流程一致，见 08）。
- 只选了 5 道判分快的题，不代表 Terminal-Bench 2.0 的整体难度分布，结果不能和排行榜比较。
