# 34 · 模型／Agent 学习路线与第一轮环境检查

日期：2026-10-28。用户选择先不调用模型，只做环境和 Oracle；随后表示会自行启动 Docker Desktop。

详细路线见 [逐步运行计划](../first-run-plan.md)：环境 → 自建任务 Oracle／no-op → 模型问答 → 一题工具 Agent → 模型／Solver 对照 → TB → SWE → JobBench → 消融／校准 → 新接入。

## 已执行

- 本机会话架构 arm64，uv、docker CLI 可找到；Ollama 未在当前 PATH 找到。
- Python Docker SDK 7.2.0；当前 .venv 未安装 mini-swe-agent、swebench 可选依赖。
- 注册任务：gsm8k、mmlu、open-qa、swebench、terminal、terminal-bench；注册存在不代表可选依赖和任务环境已可用。
- docker context 为 desktop-linux，但 `/Users/zhangde/.docker/run/docker.sock` 不存在，Docker 服务未连通。`open -a Docker` 返回找不到该应用；没有安装软件或改 Docker context。
- 运行 `.venv/bin/harness run -m none/none -t terminal -p terminal.solver=oracle --concurrency 1`，结果在 `runs/20261028-090754-none`，三个样本均 env_error，原因为无法连接 Docker。manifest、samples.jsonl、summary.json 已落盘。

## 启动 Docker 后的复查

- 用户启动后，socket 出现；在获准访问本机 Docker socket 后，确认服务端版本 **29.3.1**。最初的服务未连通已解决，不再把权限沙箱下的访问失败当成 daemon 未运行。
- 重跑 Oracle（`runs/20261028-091019-none`），并执行两个自建任务集成测试：Oracle 与 untouched environment。另行 `docker pull python:3.12-slim` 显示开始下载四个层，但数分钟后仍未完成。
- 大约五分钟后结束本轮启动的两个检查进程与镜像下载进程，避免无限等待；没有停止其他容器或更改 Docker 配置。基础镜像 inspect 仍报 No such image，Oracle 参考解尚未执行。
- 最终检查 `harness.sandbox` 标签容器列表为空。本轮没有实际创建任务容器，不能据此声称已验证运行后清理。
- 后一次运行停在准备阶段，不能作为完成的评测；两个 Docker 测试也没有获得通过／失败结论。

## 当前结论

Oracle 未通过，也未实际执行参考解。命令正常退出与进度显示 3/3 不等于完成判分；必须读取 outcome。没有真实模型调用或 API 消耗。

下一步先排查基础镜像拉取（仓库网络／Docker 代理等原因尚未确定），`docker pull python:3.12-slim` 完成后重跑三个任务和不操作对照，再核对镜像架构、判分与容器清理。JobBench 只核查了官方说明，尚未安装、接入或运行。
