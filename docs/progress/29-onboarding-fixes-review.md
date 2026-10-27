# 29 · 阶段八 O1～O3 补修复评

日期：2026-10-27。基准：master `77e71c0`（PR #22，实现 `828ec49`）。只更新评审文档，不修改实现，不调用真实模型。

## 结论

| 问题 | 本轮结论 |
|---|---|
| O1 执行隔离 | 默认 Docker、只读挂载、无网络与资源限制已实现，adapter 和自测均经执行器；源码层面原路径已修。当前会话 Docker 不可连接，5 个 onboarding Docker 回归跳过，运行验收仍需复核 |
| O2 字段映射 | 本机回归通过。答案当 input、错误 target、加载内容不稳定、实际 prompt 错误和 few-shot 泄漏均有独立检查 |
| O3 总时限 | 慢模型调用及超时误报成功已修；镜像准备与容器启动仍未完整受截止时间约束，保留一个 P2 补修项 |

真实模型自动接入仍未验收；Judge 校准、TB／SWE Solver 对照与消融继续单列。

## 验证

- 本机 `.venv/bin/python -m pytest -q`：**338 passed、23 skipped，23.67 秒**。
- `.venv/bin/ruff check .` 与 `ruff format --check .` 通过（118 files already formatted）。
- uv 默认缓存目录不可写，因此直接使用现有 .venv，没有修改依赖。
- Docker SDK ping 抛出 DockerException，没有真实构建镜像或运行容器；开发方报告的 378 passed 不是本轮独立验证结果。
- 故障复现与执行产物只写临时目录。

## 原问题复核

### O1

OnboardSpec.executor 默认 docker，示例配置也显式选择 docker。DockerExecutor 只读挂载 harness 包，仅暴露临时副本目录，不挂载正式仓库和冻结验收源包；根文件系统只读，网络关闭。Docker 不可用时返回 env_error，没有隐式降级到宿主执行。

显式 subprocess 仍可越界写宿主文件，现有测试明确验证这一点；它已被限制为可信脚本用途，不应拿来运行未知的模型候选。实际 Docker 越界写、网络与清理行为仍需复核。

### O2

新增冻结 expected.jsonl，对 input／target 独立比对，并通过实际 run_eval 捕获 Solver 发给 MockModel 的消息。参数化回归覆盖原复现 `input=r["capital"]`，会报 field_error，不是仅增加候选自测。

已披露的边界仍成立：候选与 checker 同进程，不提供针对恶意候选篡改检查器的保证。本轮没有把该工作流验收成对抗性代码的独立可信判分器。

### O3 原慢模型复现

使用现有 Script／SlowModel，规划立即返回，写入调用等待 0.15 秒，总预算 0.05 秒：

```text
elapsed = 0.053s
status = failed
stop_reason = time_limit
```

原“约 0.69 秒后仍成功”已被阻止；现有测试也覆盖重试退避、运行中的验证进程取消、截止时间后返回的通过报告不计成功。

## O3a [P2] Docker 准备与启动未受整次尝试截止时间约束

位置：`validator.py:199-215`、`225-261`、`411-412`；`agent.py:425-427`。

validate() 在检查剩余时间之前调用 executor.describe()；DockerExecutor.describe() 会调用 image()，缺镜像时同步执行 images.build()。这条链没有传入剩余时间，也不能通过 cancel() 中的 live 容器列表中止构建。Agent 只 await to_thread(validate, ...)，没有验证阶段的外层截止时间。

安全复现：保留真实 DockerExecutor／validate／OnboardAgent 路径，仅将 Docker 客户端换成假客户端：images.get() 抛 ImageNotFound，images.build() 睡 1.2 秒后返回假 image ID。模型仍用现有脚本回复；不启动 Docker，不拉取镜像。

```text
time_limit_s = 0.1
simulated_build_delay = 1.2
elapsed = 1.238s
status = failed
stop_reason = time_limit
```

最终状态已正确记为超时，但截止时间不能打断镜像构建。首次构建需要拉镜像或访问 PyPI 时，真实等待可能远大于剩余预算。即使镜像已存在，containers.run() 的创建／启动也在 container.wait(timeout=...) 之外，之后 wait 仍拿到启动前的完整 timeout，而非剩余时间。

补修验收：明确镜像准备的预算归属。如果属于整次尝试，应让准备、创建／启动与等待共用截止时间，超时／取消时终止相关工作并清理；也可以设计显式、独立限时的准备阶段，在开始尝试前完成并记录。不要只给 to_thread 套超时，让后台构建或迟到创建的容器继续运行。补冷镜像慢构建、慢创建／启动和准备中取消的回归，并在 wait 前重新扣除已用时间。

## 下一步

修 O3a，并在可用 Docker 环境复核已有隔离回归。不必为这次补修增加评测集或模型 API 调用。通过后进入本地真实试跑，按既有双线计划补真实实验与操作体验。
