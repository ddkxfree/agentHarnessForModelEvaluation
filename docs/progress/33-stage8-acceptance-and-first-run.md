# 33 · 阶段八补修复评与首次实际运行安排

日期：2026-10-28。基准：master `dd2bd3e`（PR #24）。本轮只更新文档，不修改实现、不调用真实模型。

## 复评结论

上轮 O3b 两条确定性复现已阻止，本轮在该补修范围内未发现新的阻塞项。可以将阶段八最小工作流的代码补修收口，进入真实运行验收；不等于所有 Docker 行为、真实模型接入或前十阶段全部实验都已独立验收。

复用现有 FakeDocker／fake_executor，使用真实 DockerExecutor.run：

| 场景 | cancelled | 创建数 | start | wait | 清理 |
|---|---|---|---|---|---|
| prepare 后 cancel 再 run | True | 0 | 未调用 | 未调用 | 没有创建容器 |
| 创建结果返回前 cancel | True | 1 | 未调用 | 未调用 | 已删除 |

- `.venv/bin/python -m pytest -q -rs`：**352 passed、24 skipped，28.25 秒**。
- ruff check、ruff format --check 通过（121 files already formatted）。
- 跳过项包括 Docker、TB 文件／SWE 镜像、可选依赖；本会话未独立运行 6 个 onboarding Docker 用例。开发方此前 Docker 实测见 [30](30-onboarding-o3a-fix.md)，不作为本轮新实测。
- 第 32 轮说明未随提交入库，本轮根据源码补齐简要记录，明确不是开发方原文。

## 接下来优先实际使用

先完成环境清单与一条真实链路，再扩实验规模；既有计划见 [双线计划](../post-ten-plan.md)。

1. 确认 Docker 连接、Python 依赖、模型端点、工具调用能力和镜像架构。仅检测凭据是否存在，不输出密钥；环境缺失单独报告。
2. 无模型检查三个自建终端任务：Oracle 通过、不操作失败，记录评分与清理结果。先验证题目和环境。
3. 使用可用本地模型或独立 API，先跑一个问答小样本和一道终端题，打开轨迹解释 prompt、工具调用、返回、停止原因和评分。准确率不作链路验收门槛。
4. 扩到固定 TB 小子集，做 Solver 对照或一次观察截断消融，归档配置、版本、预算、用量与失败报告。SWE 先单题 Oracle／verify，再跑 Solver。
5. 分别补真实 Judge 校准与一次真实 onboarding，保留独立人工标签／候选 diff 评审；小样本只作初步验证。

Opus 可并行完成无收费调用的环境预检、最小 smoke 配置、结果检查和 hands-on 修订。先修旧仓库地址、分支及过时准确率预期，保留既有 core／CLI 接口；本地只读结果页放在首次真实运行之后。暂不优先增加调度集群或大量新评测集。

Cursor 开发额度与 Harness 的推理端点预算分开。是否租 GPU，等小运行提供速度、用量与兼容性证据后再决定。
