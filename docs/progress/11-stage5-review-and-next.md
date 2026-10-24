# 11 · PR #6～#10 复评、本地运行与下一阶段

日期：2026-10-24。只做评审、复现和文档更新，不修改实现，不执行付费模型评测。

## 评审基准与验证范围

- 当前工作区仍在 `0bfff46`（PR #6）。没有切换、重置或更新用户工作区。
- 从本地已缓存的远端引用 `origin/cursor/stage5-solver-comparison-7316` 导出 `c950fa5cb3bb2dfc1dcdd51f35a4931bb4046d25` 到临时目录，审查了包含 #6～#10 的累积代码和 progress/06～10。
- GitHub API 在本次会话中连接失败，未在线核验 PR 状态或 CI 运行记录。提交图中有相应合并记录；这不等于本次查询了远端实时状态。
- 在最新导出源码上，用当前工作区已有虚拟环境和明确的 PYTHONPATH 运行 pytest：**179 passed、14 skipped**。
- 两个可选依赖模块 `minisweagent`、`swebench` 整体跳过；另有 Docker、镜像或 Terminal-Bench 文件条件导致的跳过。因此不能把通过数加跳过数当作全部依赖环境中的测试总数。
- 最新快照的 ruff check、ruff format --check 均通过（72 files already formatted）。
- 开发文档中的 210 passed、真实 Docker 和 Ollama 运行结果，本次未独立重跑。保留为开发方的运行记录。

## 总体结论

第五阶段已经具备可用的工程原型，适合进入后续阶段；还不能认定真实任务上的模型／Solver 对照实验已验收完成。不要继续用四核 CPU 上的 3B 模型反复跑大题，等待分数提高后才开发下一阶段。

建议先用一个小 PR 补以下 N1～N3，再推进阶段 7 的统计与诊断；真实模型试运行作为独立验证队列放到本地或有合适资源的推理端执行。N4 是文档与诊断证据补充，不要求先运行大型对照实验。

## 已通过源码和本机测试复核的进展

| 轮次 | 复核结论 |
|---|---|
| PR #6 正确性补修 | 上次 R1～R3 对应实现已修复；有限值验证、无损小数格式化、重评状态恢复有回归测试 |
| PR #7 失败可追踪 | 增加 Trajectory、完成样本立即 flush、原始输出 artifact、清理诊断、Solver 时间限制；mini 工作线程取消逻辑已实现，本机因可选依赖缺失未执行其测试 |
| PR #8 独立复核 | 增加 SubmissionScorer、新评分容器、完整补丁／日志哈希、镜像身份、缓存策略和 verify；环境身份严格复用仍有已记录限制 |
| PR #9 真实任务 | 5 题固定提交、内容指纹、镜像 digest；独立于 core 接入；Oracle／不操作的真实 Docker 结果来自开发文档 |
| PR #10 对照实验 | 禁止重复回放缓存；区分整体 Solver 比较和单参数消融；保留步骤 token、运行时间和失败类型；真实 TB／SWE 对照仍未运行 |

用 PR #8 的实现结束提交 `518f250` 与 PR #9 的实现结束提交 `0d4c646` 比较，core 没有改动。PR #10 随后为统计增加的 core 字段，不应误算成接入 Terminal-Bench 时必须改 core。

## N1 [P1] 容器启动后读取镜像信息失败，会漏清理

位置：最新快照 `src/harness/sandbox/docker.py:144-158`。

`DockerProvider.create` 先调用 containers.run 启动容器，再调用 `_image_info`。后一步抛错时尚未向 runner 返回 Sandbox，runner 的 sandbox 仍为 None，finally 无法删除已经启动的容器。

本机用假 Docker client 注入失败，调用真实 create 方法，结果：

```text
CREATE failure: SandboxError
container removed: False
```

验收：provider 对启动成功后、返回对象前的失败承担清理责任；镜像元信息查询失败时删除容器并保留原始错误；补创建和身份查询期间取消的测试，避免后台 SDK 工作完成后留下无人持有的容器。正常结果路径无需改动。

## N2 [P2] 缓存命中会漏记服务端模型身份

位置：`src/harness/core/runner.py:124-140`。

缓存命中时在 served.add 之前提前 return。同一个保存了 served_model 和 system_fingerprint 的模型输出，第二次运行仍有 output.served_identity，但 sample.served_models 和 summary.served_models 为空。

本机复现：

```text
首次：cached=False，output identity=actual-model@v1，sample identities=[actual-model@v1]
再次：cached=True， output identity=actual-model@v1，sample identities=[]
```

验收：缓存返回与新调用共用身份记录路径；缓存携带的身份表示历史响应的来源，不能声称重新验证了当前服务端版本；覆盖全部命中、部分命中和中途服务端身份变化。

## N3 [P2] 对照报告漏检实际镜像漂移

位置：`src/harness/analysis/compare.py:273-277`，以及 verify 的环境复核逻辑。

repeat_config_changes 只比较 manifest；实际镜像 ID、digest 和容器约束在 samples.jsonl 的 sandbox／grading_sandbox 里，没有进入这项检查。同一个浮动镜像标签在不同轮次解析到不同镜像时，报告无法指出这个实验混杂因素。

本机在已完成的 2×2 假模型对照记录中注入第二轮实际镜像 ID 从 A 到 B 的变化，离线重建报告：

```text
complete=True
same_samples_in_every_run=True
plain.repeat_config_changes=[]
```

complete 只表示运行齐全，本身不是错误；缺口在于没有独立的实际环境漂移标记。TB 的 digest 固定降低了该风险，但 SWE-bench 的浮动标签和本地构建环境仍有此问题。

验收：按 sample id 比较实际镜像、平台、资源／网络约束；忽略必然不同的容器 id/name。分别报告运行完整性、配置差异、实际环境漂移和可比性。verify 对记录镜像与当前评分镜像的差异也应显式标注，不能仅凭新旧分数相同让用户误解为同环境复现。

## N4 文档中的失败归因需要修正

progress/10 把 RepeatedFormatError 解释为“无法输出单个 bash 代码块”。但 `solvers/mini_swe_agent.py:97-129` 使用 BASH_TOOL 和 parse_toolcall_actions，桥接走的是结构化 tool_calls。

现有证据足以说明模型／接口／协议组合没有稳定工作，尚不足以定位为代码块格式问题。补充一两个原始模型响应、工具调用和具体解析错误；核对实际工具 schema、参数与提示词是否一致，再归因。无需为了这项文档修正启动一小时的大题评测。

同样，temperature=0、seed=0 下重复轨迹不同可以作为观测事实；这并不单独证明差异来自 CPU 非确定性，也可能涉及任务初态、并发或服务端实现。应保留这些候选原因。

## 本地运行条件与分工

本机只读取非敏感硬件字段得到：Apple M5、32 GB 内存、10 核 CPU。当前 PATH 未找到 ollama 命令；当前会话无法使用 Docker 测试。这是运行条件待准备，不是 Harness 不能本地运行。

- 云端：写代码、MockModel 单元测试、可用时执行 Docker Oracle／不操作校验。四核限制主要影响云端 VM 内自行运行的 Ollama；不能据此认为 Cursor 使用的云端开发模型也只在四核 CPU 上推理。
- 本地：Harness 与 Docker 执行环境；Ollama 原生运行在 macOS 上利用 Apple GPU，再由 Harness 请求其接口。先确认实际 GPU 使用和上下文容量，不承诺未经测量的速度。
- 需要较强模型时：Harness 调用外部模型 API，推理由提供方承担，本地／云端负责调度和任务执行；不要求本机能容纳该模型权重。
- M5 是 arm64；已选 SWE-bench 镜像名是 x86_64，部分 TB 镜像的可用架构也需预检。模型推理加速并不能消除 amd64 容器在 arm64 上的兼容性与仿真成本。

参考：[Ollama Apple GPU 支持](https://docs.ollama.com/gpu)、[用 ollama ps 检查 GPU 使用](https://docs.ollama.com/faq)、[Cursor 云端与自有执行机器的区别](https://cursor.com/docs/cloud-agent/self-hosted/choose-runtime)。本轮未安装软件、下载模型或运行真实模型。

## 接下来给 Opus 的开发批次

1. 小补修：N1～N3 的实现和回归测试，修正 N4 文档，提交后复评。不要为该批次反复运行 3B 的 TB／SWE 长任务。
2. 阶段 7 最小版：统一 run／compare 的 epochs、缓存命名空间与恢复语义；配对比较、置信区间和失败样本 Markdown 报告；不要另起一套重复实验引擎。阶段 10 已有 compare 骨架，应复用。
3. 阶段 10 最小版：补 token 预算与能力／协议记录；做一次真实单变量消融。预算不同时明确属于预算敏感性实验，不声称是等预算策略对照。
4. 阶段 6 最小版：单点 rubric、成对互换、多裁判聚合、独立小标注集；顺序不一致记 unstable／inconclusive，不直接等同质量平局；裁判请求也要缓存与归档。
5. 阶段 8 最小版：在隔离开发目录生成一个 Task adapter 的 diff，使用冻结的正反例独立验收，不让接入 Agent 修改标准答案或官方评分规则。

阶段 9 的最小集成已覆盖 SWE-bench、mini-swe-agent 和 5 道真实 TB 任务；它与第五阶段重叠，不需要重新开发一套同名适配器。后续重点是复核一致性和更多任务类型，而非追求接口数量。

## 可玩的最小试运行，不必等十阶段全部完成

1. 同步到经过评审的新代码后，准备本地依赖、Docker、模型端点，先执行 Oracle 和不操作校验。
2. 两种 Solver 各跑一个自建任务，检查原始 tool_calls、最终产物和完整轨迹；确认协议没有失配再放大样本数。
3. 自建 3 题做一轮对照；真实 TB 选 1～2 题、SWE-bench 选 1 题做单次冒烟。
4. 无模型调用地重建 compare-report、执行 SWE verify，检查摘要、哈希和清理记录。
5. 最后才执行 3 次重复和完整对照。样本小、成功率低并不阻止软件链路验收，但不能据此给 Agent 排名。

真实 TB／SWE 的两种 Solver 对照、真实消融和更强模型实验保留为待验证项；后续可以推进开发，但不能在路线图上把实验验收标为完成。
