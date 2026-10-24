# 12 · 第五阶段补修：容器清理、缓存身份、环境漂移、归因更正

日期：2026-10-24。对应 [11](11-stage5-review-and-next.md) 的 N1～N4。本轮没有运行任何模型评测。

另外发现：PR #7～#10 是叠加 PR，各自合并进了上一层分支，没有进入 master。已开 PR #11 把它们合并到 master（只有 `docs/progress/README.md` 表格冲突，两边条目都保留）；本轮补修叠在 #11 之上。

## N1 容器启动后、返回前失败会漏清理

`DockerProvider.create`（`sandbox/docker.py`）在返回 Sandbox 之前负责清理：

- 读取镜像信息失败：删除已启动的容器，再抛出**原始**错误；删除也失败时，把删除错误作为 note 附在原始错误上，不覆盖它。
- 读取镜像信息期间被取消：同样删除容器（删除操作用 `shield` 保护），然后继续传播取消。
- 启动容器期间被取消：Docker SDK 调用在线程里继续执行，协程无法中止它。启动调用被 `shield` 包住，取消时登记一个回调：容器建好后立即删除。`SandboxProvider.drain()`（新增，默认空操作）等待这些后台删除完成；`run_samples` 结束时（包括异常和取消）都会调用。
- 正常路径不变。

测试：`tests/test_docker_provider.py`（5 例，假 Docker client）覆盖正常创建、镜像信息报错（普通异常和 Docker 异常）、删除失败附 note、启动中取消、读镜像信息时取消。`tests/test_docker_sandbox.py::test_create_never_leaves_a_container_behind`（2 例）在真实 Docker 中注入同样的失败和取消，按标签确认没有残留容器。

## N2 缓存命中漏记服务端模型身份

- 缓存命中时，缓存输出携带的身份记入新字段 `SampleResult.cached_served_models`；新调用的身份仍记入 `served_models`。两者分开：缓存里的身份只说明那条历史响应来自哪个服务端版本，不代表本次验证了当前服务端。
- summary 和 compare 报告同时给出 `served_models` 与 `cached_served_models`。

测试：`test_verification.py::test_cached_outputs_keep_their_served_identity_separately`：第一次运行 3 题（服务端 b1），第二次 6 题（服务端已换成 b2），前 3 题命中缓存、后 3 题新调用，两类身份分别为 b1 和 b2；第三次全部命中，`served_models` 为空。中途换版本（同一次运行内）沿用已有测试。

## N3 实际环境漂移检查

新增 `analysis/environment.py`：从 `sandbox` 和 `grading_sandbox` 提取实际环境身份（镜像 ID、digest、平台、生效的内存／CPU／网络），去掉每个容器必然不同的 `container_id`、`container_name`。

- **compare**：`environment_drift` 按样本列出在各次运行中取值不同的字段，以及每次运行的取值。报告把四项检查分开给出：`complete`（运行齐全且完成）、`same_samples_in_every_run`、`repeat_config_changes`（配置差异）、`environment_drift`（实际环境差异）；`comparable` 要求四项都通过。命令行输出会逐条提示漂移。
- **verify**：每行新增 `environment_changes`（记录的环境与本次评分容器的差异）；汇总新增 `environment_changed` 样本列表，命令行提示"在与记录不同的环境中评分"。分数一致但环境不同时，状态仍是 `agree`，但会被单独标出，不再让人误以为是同环境复现。

测试：`test_compare.py::test_actual_image_drift_is_reported_separately`（第二轮 plain 的某题镜像 ID 从 A 变为 B，`complete` 和配置检查都通过，但 `environment_drift` 指出该样本和字段，`comparable` 为假）、`test_clean_comparison_is_comparable`；`test_verification.py::test_verify_flags_a_different_grading_environment`。

## N4 文档归因更正

progress/10 写的"3B 模型不能稳定输出 mini 要求的单个 bash 代码块格式"不对。`solvers/mini_swe_agent.py` 用 `BASH_TOOL` 和 `parse_toolcall_actions`，走的是结构化 tool_calls。

重新检查保存的轨迹（`runs/compare-20261024-092848-terminal/mini/`，没有重新调用模型）：

- 9 个样本共 51 步：19 步有结构化工具调用，32 步没有。
- 没有工具调用的 32 步中，26 步的回复正文是 `[Makes bash tool call with {"command": "..."} as arguments]` 这样的文字，只有 1 步用了代码块。
- 这句话出自 mini-swe-agent 2.4.6 自带的 `config/mini.yaml`：它在 `instance_template` 的"正确回复示例"里用这行文字代表一次工具调用。3B 模型把示例当作要照抄的格式写进正文，而没有发出工具调用；部分步骤同时写了这行文字并发出了真正的调用（有工具调用的 19 步里有 13 步）。
- 例：`terminal/hello-file` 第一次 mini 运行，第 0 步正常调用 `ls -la`，第 1～3 步都只在正文中写出 `cat > /app/greeting.txt ...` 的调用文字，连续格式错误后以 `RepeatedFormatError` 结束。
- 工具 schema 与提示词一致（都只有一个 `bash` 工具、一个 `command` 参数），问题不在 schema 不符。

结论：失败来自"小模型 + mini 默认提示词中的示例写法 + 结构化工具调用协议"的组合，既不是代码块格式问题，也不能说明 mini 的策略更差。progress/10 中的对应句子已加删除线并指向本文。

同样，temperature 0、seed 0 下重复轨迹不同只是观测事实，可能的原因包括 Ollama 的数值非确定性、任务初态和服务端实现，未定位；progress/10 已改为这一表述。

## 验证

`uv run pytest -q`：221 passed（本机 Docker 实际运行）；`ruff check`、`ruff format --check` 通过。
