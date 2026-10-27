# 30 · 阶段八补修 O3a：镜像准备与容器启动受时限和取消约束

日期：2026-10-27。基准：master `aa71a50`（含 [29](29-onboarding-fixes-review.md) 的复评）。只修 O3a，并在可用 Docker 的环境中复核已有隔离回归；不新增评测集，不调用真实模型。**状态：补修与 MockModel／Docker 回归完成，真实模型接入仍待验证。**

## 预算归属

按复评给出的第二种方案：镜像准备是**独立限时、在尝试开始前完成并记录**的阶段，不计入 `time_limit_s`。

- 规格预算新增 `prepare_timeout_s`（默认 900 秒）。首次构建要拉基础镜像、访问 PyPI，时间与网络有关，不应挤占模型和验证的时间；镜像已存在时准备只是一次查询。
- Agent 先核对源包哈希，再准备执行器，**准备完成后才开始计时**。结果写入尝试记录的 `preparation`（镜像 tag、ID、Dockerfile、是否新构建、耗时）。
- 准备超时：尝试以 `prepare_timeout` 结束，类别 `env_error`，不调用模型；Docker 本身不可用仍是 `env_error`。
- 直接调用 `validate()`（如 `onboard-verify`）时，准备同时受 `prepare_timeout_s` 和传入的总 `timeout` 约束，超时记为 `validation_timeout`。`describe()` 不再隐式构建镜像。

## 准备、创建、启动、等待都可终止

执行器接口新增 `prepare(timeout)`。`DockerExecutor` 的实现：

- 镜像查询在辅助线程中执行，超时或取消时放弃等待。
- 镜像构建放在**独立子进程**中，用 Docker SDK 流式构建。超时或 `cancel()` 时 kill 整个进程组，连接断开后 Docker daemon 会中止构建、删除构建容器。本机已实测：构建中途 kill 客户端后，构建容器随即消失，没有留下镜像。只是在线程外层套超时做不到这一点，后台构建会继续运行。
- `run()` 在入口确定截止时间：准备（已就绪时为空操作）、容器创建（`containers.create`）、启动（`start`）都在辅助线程中执行，只给剩余时间；在放弃之后才创建好的容器，由辅助线程立即删除。`wait()` 拿到的是扣除准备、创建和启动耗时之后的剩余时间。
- 创建或启动超时记为 `timed_out`，对应 `validation_timeout`。容器无论成败都会被删除，启动超时的容器先 kill 再删除。
- `cancel()` 改为线程事件：正在进行的构建、创建、启动和等待都会停止，之后再启动的工作也会立即放弃。

`SubprocessExecutor.prepare()` 不做任何事。

## 回归

假 Docker 客户端（不需要 Docker，复评环境可运行）：

| 测试 | 场景与断言 |
|---|---|
| 冷镜像慢构建 | 复评复现：总时限 0.1 秒，构建要 60 秒，`prepare_timeout_s=1`。5 秒内结束，`prepare_timeout`／`env_error`，模型零调用，构建进程已被 kill |
| 准备不占尝试时间 | 构建 1 秒、总时限 0.8 秒：尝试仍进入规划、写入和验证，`prepare_s ≥ 1` |
| 准备中取消 | 构建进程被 kill，尝试记为 `cancelled`，模型零调用 |
| validate 中的准备 | `timeout=0.5` 且冷镜像慢构建：3 秒内 `validation_timeout`，详情说明在准备阶段，构建进程已被 kill |
| 慢创建 | 创建要 1 秒、时限 0.3 秒：0.8 秒内 `timed_out`；创建完成后迟到的容器被删除，没有等待它 |
| 慢启动 | 启动要 1 秒、时限 0.3 秒：0.8 秒内 `timed_out`，容器被 kill 并删除 |
| 等待只拿剩余时间 | 创建 0.3 秒、启动 0.2 秒、时限 1 秒：`wait()` 收到的超时 ≤ 0.55 秒 |

真实 Docker（不可用时跳过）：新增慢构建测试，Dockerfile 中 `RUN sleep 60`，`prepare(timeout=4)` 在 10 秒内抛出 `PrepareTimeout`，之后不再有该构建的容器。

## Docker 隔离回归复核

复评环境 Docker 不可连接，[28](28-onboarding-review-fixes.md) 的容器用例被跳过。本轮在 Docker 29.1.3 上运行，6 个 Docker 用例全部通过：

```text
PASSED test_docker_blocks_adapter_writes_outside_its_scratch_directory
PASSED test_docker_blocks_writes_to_the_harness_package
PASSED test_docker_blocks_self_test_writes_outside_and_the_network
PASSED test_docker_validation_matches_the_subprocess_one
PASSED test_docker_kills_and_removes_containers_on_timeout_and_cancel
PASSED test_docker_aborts_a_slow_image_build
```

另外，用新的 Dockerfile tag 手动走了一次真实的子进程构建，构建成功；第二次调用复用已准备好的镜像。

## 验证

- `uv run pytest -q`：386 passed（含 Docker 用例；onboarding 测试 60 个）。
- 只装基础依赖的环境：361 passed、8 skipped。
- `ruff check`、`ruff format --check` 通过。

## 仍然成立的边界

- 候选代码与检查代码同进程，不针对恶意候选提供可信判分保证（[28](28-onboarding-review-fixes.md) 已披露）。
- 真实模型接入仍待本地或通过 API 运行，命令见 [28](28-onboarding-review-fixes.md)。首次运行会在尝试开始前构建镜像，最多等 `prepare_timeout_s`。
