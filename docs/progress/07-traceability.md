# 07 · 失败可追踪（第五阶段加固，第二轮）

日期：2026-10-24。基于 06（R1～R3 修复）之上开发。

## 目标

05 复评列出的第二轮验收项：

1. 第二次模型调用失败时，第一步记录仍然保留；
2. 单个样本完成即落盘；
3. 原始工具输出单独保存，模型看到的截断内容可追踪；
4. 清理失败时记录容器身份与诊断信息；
5. 样本总超时与取消能停止工作并清理资源，mini-swe-agent 的工作线程也要停下来。

## 修复前的状态（源码复核）

- runner 只在 Solver **正常返回**后才拿到 `steps`。Solver 中途抛异常时，已经执行过的步骤全部丢失。
- `run_eval` 等整个任务的所有样本都结束，才写 `samples.jsonl`。
- 基线 Agent 的 `observations` 是截断后的文本，完整输出没有保存。mini-swe-agent 正好相反：记录的是完整输出，模型实际看到的版本只留在 `messages` 里。
- `sandbox.close()` 的异常被 `contextlib.suppress` 吞掉，也没有记录容器 id。
- 没有样本级的总时长限制。只有单条命令超时（`command_timeout`）和单次模型调用超时。

## 这一轮做了什么

### 1. 中途失败保留轨迹（`core/trajectory.py`，新增）

- 新增 `Trajectory`，记录单个样本的 `messages`、`steps` 和 `artifacts`。runner 为每个样本创建一个，并通过 `contextvars` 设为当前轨迹。Solver 用 `current_trajectory()` 取得后边执行边写入，不再自己维护私有列表。
- Solver 正常返回时，以 `SolveResult` 为准。抛异常时（模型、Solver、环境任一方），runner 把轨迹里已有的 `messages` 和 `steps` 写进结果。
- 基线 Agent 在**调用工具之前**就把当前步骤加入列表，命令执行中途出错也能看到是哪一步、调用了什么。
- mini-swe-agent：`HarnessModel` 记录的步骤直接写入轨迹。`DefaultAgent` 的消息在工作线程结束时（无论成功、失败还是取消）复制进轨迹。
- 不用这个接口的第三方 Solver 不受影响，只是失败时没有部分轨迹。Solver 协议没有改。

### 2. 单样本完成即落盘（`core/runner.py::run_eval`）

- 每个样本完成后立即追加一行到 `samples.jsonl` 并 `flush`。
- 整个任务结束后，按样本原始顺序重写一次文件，与以前的格式一致。
- 运行被中断时，manifest 记为 `interrupted`，已完成的样本都在文件里。

### 3. 原始工具输出单独保存

- `Step.observations` 现在统一表示**模型看到的内容**（截断后）。
- 新增 `Step.raw_outputs`，与 `observations` 一一对应：
  - 输出被截断时，值为 artifact 名，完整输出写入 `<run>/<task>/artifacts/<样本目录>/step-<i>-call-<j>.txt`；
  - 没有截断时为 `None`。
- 每个 artifact 在 `SampleResult.artifacts` 中记录名称、SHA-256、字节数和相对路径。直接调用 `run_samples` 且不给 `artifact_dir` 时，只记录哈希，不写文件。
- 基线 Agent：`ExecResult.combined()` 截断前后不同，即保存完整输出。
- mini-swe-agent：
  - 环境先记录原始输出；
  - `format_observation_messages` 再把它替换成模板渲染后、模型实际看到的内容；
  - 原始输出没有完整出现在渲染结果里（mini-swe-agent 的模板会截断长输出），就另存为 artifact。
- 样本目录名是 `样本 id 的可读部分 + id 哈希前 8 位`，避免不同 id 清洗后重名。
- `harness show --sample` 在被截断的输出下方标出完整输出文件的路径和大小。

### 4. 清理失败可追踪

- `Sandbox` 新增 `describe()`，默认返回类型名。`DockerSandbox` 返回以下字段，创建沙箱后立即写入 `SampleResult.sandbox`：
  - 容器完整 id、容器名；
  - 镜像名，以及实际使用的镜像 ID（`sha256:...`）。
- `close()` 抛异常，或超过 `CLOSE_TIMEOUT`（120 秒）没有返回，都会把错误写入 `SampleResult.cleanup_error`，并输出带沙箱身份的 warning 日志。
- 清理失败不改变样本的 outcome（结果本身有效），但 summary 新增 `cleanup_errors` 计数，可以发现资源泄漏。

### 5. 样本总时长与取消

- `Task` 新增 `time_limit`（秒，`None` 为不限），只限制 Solver 阶段，评分有自己的超时。数值写入 manifest。
- `terminal` 和 `swebench` 任务新增同名参数，默认分别为 900 和 3600 秒，可用 `-p terminal.time_limit=...` 覆盖。
- 超时后 runner 取消 Solver，用轨迹里已有的内容构造 `finish_reason="time_limit"` 的输出，然后**照常评分**，与 Terminal-Bench 在 Agent 超时后仍跑测试的做法一致。
  - 测试通过记为 `correct`；
  - 不通过记为新 outcome `time_limit`，和 `step_limit` 同类，计入失败。
- 只有 runner 自己的计时器到期才算 `time_limit`。Solver 内部抛出的 `TimeoutError` 仍是 `solver_error`。
- 基线 Agent 是协程，取消会直接打断正在等待的模型调用或命令。随后 `close()` 删除容器，容器里的进程也一起结束。
- mini-swe-agent 跑在工作线程里，只取消外层协程，线程会继续执行。本轮的处理：
  - 工作线程通过 `_Bridge.run` 把每次模型调用和命令提交回事件循环。取消时 `_Bridge.cancel()` 取消所有进行中的调用，并拒绝之后的新调用；
  - 线程里随即抛出 `_Cancelled`。它继承 `BaseException`，不会被 mini-swe-agent 的 `except Exception` 吞掉，Agent 循环因此退出；
  - Solver 等工作线程真正结束（最多 `cancel_grace`，默认 30 秒）后才返回，之后才评分和删除容器；
  - 线程在宽限期内没有停下时，记为 `solver_error`（"worker did not stop"），不评分，但仍然删除容器。

## 验证

`uv run pytest -q`：152 passed（Docker 集成测试 8 个，本机 Docker 29.1.3 实际运行）。`ruff check`、`ruff format --check` 通过。

| 测试 | 验收项 |
|---|---|
| `test_traceability.py::test_model_failure_on_second_call_keeps_the_first_step` | 1：第 2 次调用失败，`api_error`，第 1 步的文本、命令输出、退出码和消息都在 |
| `test_traceability.py::test_solver_crash_keeps_steps_up_to_the_crash` | 1：执行命令时 Solver 出错，出错的那一步也在 |
| `test_traceability.py::test_completed_samples_are_on_disk_before_the_run_finishes` | 2：第 4 个样本卡住时取消运行，前 3 个已在 `samples.jsonl`，manifest 为 `interrupted` |
| `test_traceability.py::test_truncated_tool_output_is_saved_as_an_artifact`（有／无目录） | 3：截断的观察、完整输出文件、哈希与大小 |
| `test_mini_swe_agent.py::test_truncated_observations_are_kept_as_artifacts` | 3：mini-swe-agent 模板截断 30000 字符输出时另存 |
| `test_traceability.py::test_cleanup_failure_is_recorded_with_the_sandbox_identity`、`test_hanging_cleanup_times_out` | 4：清理异常与清理卡住 |
| `test_traceability.py::test_time_limit_cancels_the_agent_and_still_scores`（通过／不通过） | 5：命令被取消、容器关闭、照常评分 |
| `test_traceability.py::test_solver_errors_are_not_mistaken_for_the_time_limit` | 5：Solver 自己的 `TimeoutError` 不算超时 |
| `test_mini_swe_agent.py::test_time_limit_stops_the_worker_thread`（卡在命令／卡在模型调用） | 5：Solver 返回时工作线程已结束；之后没有新的模型调用和命令 |
| `test_mini_swe_agent.py::test_worker_that_does_not_stop_is_a_solver_error` | 5：线程不响应取消时记为 `solver_error`，容器仍被关闭 |
| `test_docker_sandbox.py::test_time_limit_stops_a_hung_agent_and_removes_its_container`（agent／mini） | 5、4：真实容器中 `sleep 600` 在 3 秒后被终止，outcome 为 `time_limit`，容器已删除，记录了镜像 ID |

反向检查：把 `bridge.cancel()` 临时去掉，mini-swe-agent 的两个线程取消测试都失败（线程一直等到宽限期结束）。

用 Oracle 在 Docker 上跑 `harness run -m none/none -t terminal -p terminal.solver=oracle`：3/3 通过，每个样本都记录了容器 id 和 `sha256:` 镜像 ID，manifest 中有 `time_limit: 900.0`。

## 对已有结果的影响

- `samples.jsonl` 新增字段 `artifacts`、`sandbox`、`cleanup_error` 和 `Step.raw_outputs`，都有默认值，旧文件仍可读取。
- mini-swe-agent 的 `Step.observations` 从原始输出改为模型看到的内容。模型收到的消息没有变化，所以 Solver 指纹不变。
- `terminal`、`swebench` 的参数多了 `time_limit`，manifest 的 `params` 因此变化，`harness diff` 会显示出来。
- 新 outcome `time_limit` 出现在 summary 的 `outcomes` 和 `rates` 里。

## 未做／留到下一轮

- 运行中断后**续跑**（跳过已落盘的样本）没有实现。目前依靠模型调用缓存避免重复花费，epochs 与续跑语义留给阶段 7。
- 评分阶段没有总时长限制，只有各评分器自己的超时（终端任务的 `verifier_timeout`、SWE-bench 的 `eval_timeout`）。
- 第三轮：完整 patch／日志与哈希、镜像 digest、服务端模型标识、缓存策略，以及在新容器中独立评分。本轮记录的 `image_id` 和 artifact 机制为第三轮打了基础。
