# 31 · 阶段八 O3a 补修复评

日期：2026-10-27。基准：master `ff27973`（PR #23，实现 `f66b7c6`）。只修改评审文档；复现使用临时目录与假 Docker 客户端，不运行真实模型、不修改实现。

## 结论与检查

- 镜像准备阻塞尝试的主路径已修：独立 prepare_timeout_s，准备完成后启动尝试计时并记录 preparation；describe 不再隐式构建镜像。
- 慢构建超时／取消、validate 准备限时、慢创建和迟到删除、慢启动、wait 扣除准备与启动耗时的现有回归均通过。
- O1／O2 的此前实现保留，字段映射回归通过。本会话仍不能连接 Docker；开发方文档记录的 Docker 29.1.3 实测不作为本轮独立验证。
- **O3a 仍剩一个 P2：取消与容器创建结果交接之间的竞态，可能在取消之后启动候选容器。**
- `.venv/bin/python -m pytest -q -rs`：**345 passed、24 skipped，28.50 秒**。跳过项包括 Docker、Terminal-Bench 文件／SWE 镜像、mini-swe-agent／swebench 可选依赖，6 个 onboarding Docker 用例均跳过。
- ruff check、ruff format --check 通过（120 files already formatted）。没有把开发方报告的 386 passed 当成本轮独立实测。

## O3b [P2] 已取消的执行器仍可能启动新容器

位置：`validator.py:258-268`、`273-274`、`371-383`、`415-419`。

_bounded() 只在 done.wait() 没等到结果时检查取消标志。如果取消前已完成，或者取消与快速返回发生在同一个等待窗口，结果会直接返回。准备好的镜像路径也直接返回；容器登记到 _live 时没有与 cancel() 原子交接或重新检查取消标志。因此以下顺序成立：

1. 创建请求已经发出，容器尚未登记到 _live。
2. cancel() 设置事件，但 _live 为空，没有容器可 kill。
3. 创建请求立即返回，done.wait() 返回 True，跳过取消检查。
4. 容器被登记，快速 start() 同样跳过取消检查，接着进入 wait()。

候选代码因而可能在调用方已取消、尝试已记录 cancelled 后继续执行，直到运行完毕或原等待时限结束。最终 remove 不等于取消时已阻止执行。

### 确定性复现

复用 tests/test_onboard.py 的 FakeDocker／fake_executor，先 prepare()。假客户端 create 方法模拟在创建结果返回前收到取消：

```python
def create(image, argv, **kwargs):
    executor.cancel()
    return fake.container

fake.create = create
```

给 fake.container.start 加标记，记录 start 时取消事件是否已经置位，再调用真实 DockerExecutor.run。没有容器或模型的外部操作。

```text
case = cancel_during_create
started_after_cancel = True
timed_out = False
wait_called = True
killed = False
removed = True
```

另一个复现是 prepare 后先 cancel 再调用 run，结果相同：已经取消的执行器仍创建、启动、等待容器。这违背 Executor 协议中“取消之后启动的工作也被终止”的约定。

### 补修验收

取消标志应在准备／工作提交前、拿到结果后和容器登记交接处生效；即使结果立即就绪，也不能跳过取消。创建结果在取消后交回时，应删除容器、不能再启动；登记与 cancel 的交接应保证没有漏杀窗口。已取消执行器不能再开始构建或候选执行。

补两个不依赖 Docker 的确定性回归：prepare 后 cancel 再 run；创建结果返回前 cancel。断言 start／wait 未被调用，已创建容器被清理，返回停止结果。保留现有慢创建、慢启动、构建取消回归。

## 后续

这是 O3a 补修内的取消竞态，不是原来的镜像构建时限问题仍未修改。补完这个小项后继续本地真实试跑；真实 Docker 隔离和真实模型接入仍按各自证据验收。
