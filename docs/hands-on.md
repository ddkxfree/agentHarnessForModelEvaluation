# 上手练习指南

在自己电脑上从零跑起来，按顺序做，每一步都写了"应该看到什么"。

## 0. 准备工具

| 工具 | 用途 | 安装 |
|---|---|---|
| git | 拉代码 | macOS 自带，或 `xcode-select --install` |
| uv | 管理 Python 和依赖 | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| Ollama | 本地跑模型，不需要 API key | <https://ollama.com/download> |
| Docker Desktop | Agent 评测的沙箱（第 3 步起需要） | <https://www.docker.com/products/docker-desktop/> |

Apple Silicon（M 系列）的 Mac：SWE-bench 镜像只有 x86_64 版本，需要在 Docker Desktop 设置里打开 "Use Rosetta for x86_64/amd64 emulation"，并把内存调到 8 GB 以上。

## 1. 拉代码、装依赖

```bash
git clone https://github.com/ddkxfree/announce.git agent-harness
cd agent-harness
git checkout cursor/eval-harness-platform-7316   # 合并前代码在这个分支上
uv sync --extra swebench                          # 第一次会下载 Python 和依赖
uv run pytest -q                                  # 应该全部通过（没有 Docker 时部分测试显示 skipped）
uv run harness list-tasks                         # 应该看到 gsm8k mmlu swebench terminal
```

## 2. 问答类评测（只需要 Ollama）

```bash
ollama pull qwen2.5:0.5b-instruct
uv run harness run -c configs/ollama-smoke.yaml
```

应该看到：一张汇总表，GSM8K 20 题、MMLU 40 题，准确率在 0.4 左右，置信区间很宽。结果在 `runs/<run_id>/` 下。

练习：

```bash
uv run harness show runs/<run_id>                                   # 看汇总和"抽取方式分布"
uv run harness show runs/<run_id> -t gsm8k --outcome wrong -n 3     # 看答错的题
uv run harness run -c configs/ollama-smoke.yaml                     # 再跑一次：全部命中缓存，几秒结束
uv run harness run -c configs/ollama-smoke.yaml --temperature 0.8   # 采样运行默认不复用缓存
uv run harness diff runs/<第一次> runs/<最后一次>                    # 哪些题因为温度变化翻转了
```

思考：准确率没变，但有题目对错互换，这就是"指标抖动"。20 道题能不能说明模型好坏？

## 3. Agent 评测：终端任务（需要 Docker）

先确认 Docker 在运行（`docker ps` 不报错）。

```bash
# 3.1 不用模型，先用参考答案验证任务本身没问题：应该 3/3
uv run harness run -m none/none -t terminal -p terminal.solver=oracle

# 3.2 用 3B 模型配基线 Agent
ollama pull qwen2.5:3b-instruct
uv run harness run -c configs/terminal-ollama.yaml

# 3.3 同一个模型换成 mini-swe-agent
uv run harness run -c configs/terminal-ollama.yaml -p terminal.solver=mini

# 3.4 看每一步做了什么
uv run harness show runs/<run_id> --sample terminal/count-errors
```

观察重点：

- `count-errors` 有陷阱，直接 `grep -c ERROR` 会得到 211，正确答案是 96。模型有没有掉进去？
- 两种 Agent 的"结束原因"有什么不同？mini-swe-agent 下小模型常见 `RepeatedFormatError`：它把 prompt 里的示例文字照抄了出来，而不是真的调用工具。

## 4. Agent 评测：SWE-bench（需要 Docker，镜像较大）

```bash
# 4.1 先拉两道题的镜像，每个 3～4 GB
docker pull swebench/sweb.eval.x86_64.django_1776_django-11099:latest
docker pull swebench/sweb.eval.x86_64.pytest-dev_1776_pytest-5227:latest

# 4.2 用参考补丁验证判分链路：两题都应该是 resolved
uv run harness run -m none/none -t swebench -p swebench.solver=oracle \
  -p 'swebench.ids=[django__django-11099, pytest-dev__pytest-5227]'

# 4.3 基线 Agent / mini-swe-agent
uv run harness run -c configs/swebench-ollama.yaml
uv run harness run -c configs/swebench-ollama.yaml -p swebench.solver=mini
```

在 CPU 上，3B 模型一道题可能要十几分钟，而且基本解不出来，这是正常的。看 `harness show` 里的 `extracted`（`empty_patch`、`unresolved` 等）和 `details.failed_tests`，理解它失败在哪一环。

小提示：Ollama 默认上下文窗口比较小，SWE-bench 的多轮对话很快就会超出，超出的部分会被静默截掉。可以这样启动 Ollama 加大窗口（更占内存）：

```bash
OLLAMA_CONTEXT_LENGTH=16384 ollama serve
```

## 5. 换成更强的云端模型

SWE-bench 要有意义的分数，至少需要比较强的模型。以阿里云百炼为例：

```bash
export DASHSCOPE_API_KEY=sk-...
uv run harness run -m dashscope/qwen-plus -t swebench \
  -p 'swebench.ids=[django__django-11099, pytest-dev__pytest-5227]' -p swebench.solver=mini
```

DeepSeek 用 `-m deepseek/deepseek-chat` 加 `DEEPSEEK_API_KEY`。其他兼容 OpenAI 接口的服务用 `-m openai/<模型名> --base-url <地址>`。

## 6. 自己动手的练习

由易到难：

1. 给 GSM8K 换一个 prompt 模板，跑完用 `harness diff` 对比。
2. 照着 `tasks/terminal_data/` 的格式写一个新的终端任务，先保证 Oracle 能通过，再让模型做。
3. 在 SWE-bench 里加 1～2 道新题（`-p 'swebench.ids=[...]'`，先 `docker pull` 对应镜像），先跑 Oracle。
4. 读 `solvers/mini_swe_agent.py`，理解怎么不改一个现成 Agent 的源码就把它接进来；试着改它的步数上限、换成别的 mini-swe-agent 配置。

## 常见问题

| 现象 | 原因和处理 |
|---|---|
| `cannot connect to Docker` | Docker Desktop 没启动 |
| 大量 `env_error` | 看 `harness show` 里的 error；镜像没拉到或者内存不够最常见 |
| 中途 Ctrl+C 后残留容器 | `uv run harness sandbox-cleanup` |
| 想重新调用模型，不用缓存 | 加 `--no-cache`；temperature > 0 时默认就不复用缓存（`--cache-policy auto`） |
| 想确认 SWE-bench 的判分可复现 | `uv run harness verify runs/<run_id>`：用保存的补丁在新容器里重新判分，不调模型 |
| `the swebench task needs the optional dependency` | 运行 `uv sync --extra swebench` |
