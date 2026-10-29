# 35 · Docker 7897 代理配置、Python 镜像与三题正反例验收

日期：2026-10-28。代码基准 `3bc7e31`。用户授权修复 Docker 镜像下载，使用国内源或现有代理；沿用先前无模型环境／Oracle 检查的范围。本轮没有修改 Python 实现，没有模型 API 调用。

## 网络检查与配置

- 配置前 `docker image inspect python:3.12-slim` 返回 No such image。
- 直连 Docker 官方 registry／认证接口均 ConnectTimeout；经过 `http://127.0.0.1:7897` 分别返回 401（正常认证挑战）、200。后续清单探测曾出现 SSL 断连，保持证书校验后再次探测可返回正常认证挑战；不能声称代理永不抖动。
- daemon 中显示的 `http.docker.internal:3128` 是 Desktop 内部中转，不代表已经配置到 7897。核对字段名时使用实际 HttpProxy／HttpsProxy，不能把读取错误字段产生的空值当作“无代理”。
- 通过 Docker Desktop 界面核查：Resources → Proxies，原 Docker Desktop proxy 为 System proxy，Containers proxy 为 Same as host proxy。
- 将 Docker Desktop proxy 改为 Manual configuration，HTTP、HTTPS 都设为 `http://127.0.0.1:7897`；Containers proxy 保留 Same as host proxy，bypass 留空。点击 Apply 后页面显示已保存、Apply 禁用、Engine running。
- 没有增加国内镜像源，没有关闭证书验证、没有更改其他项目容器。若需撤回，可在同一界面恢复 System proxy；本机代理服务停止后，依赖该地址的拉取会受影响。

首次实际 pull 遇到 EOF，重试一次成功：

```text
docker pull python:3.12-slim
Status: Downloaded newer image for python:3.12-slim
Digest: sha256:6b25cbe6577518889d553cdd5a5da97d259348d8f6e45b527dd731caaba27a07
```

独立 inspect 确认镜像存在、平台 linux/arm64；`docker run --rm --network none python:3.12-slim python --version` 输出 Python 3.12.16。

## 三题参考解与不操作对照

Oracle 在这里意为参考解求解器，与 Oracle 数据库无关。它执行 task 的 solution/solve.sh，不调模型，用于验证环境和判分器。

```bash
.venv/bin/harness run -m none/none -t terminal -p terminal.solver=oracle --concurrency 1
.venv/bin/python -m pytest -q tests/test_docker_sandbox.py -k 'oracle_solves_every_builtin_terminal_task or untouched_environment_fails_every_builtin_terminal_task' -rs
```

归档：`runs/20261028-094709-none`，三个样本均 correct、score=1.0：

| 任务 | 参考解 | 不操作 |
|---|---|---|
| count-errors | 通过 | 失败 |
| fix-mean | 通过 | 失败 |
| hello-file | 通过 | 失败 |

两个 Docker 集成测试 **2 passed、12 deselected，0.93 秒**；一个测试断言三题参考解全通过，另一个断言三题不操作全 wrong。不操作对照使用脚本化假模型，不是外部推理。

结束后 `docker ps -a --filter label=harness.sandbox` 为空，无遗留评测容器。没有执行全量测试，也不据此宣称全部 onboarding／TB／SWE Docker 用例已独立验收。

## 下一步

[逐步运行计划](../first-run-plan.md) 的环境与自建题参考解步骤已跑通。下一轮可先展示 hello-file 的题目、参考脚本和评分脚本，再选择 API 或本地模型做一次问答／工具调用；真实模型预算和服务商尚未确定，不自动开始收费调用。此前失败／中止的运行目录保留作为环境故障学习材料。
