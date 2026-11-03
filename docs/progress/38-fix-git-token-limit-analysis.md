# 38 · 首个真实 TB 模型运行：token 上限与未解决的合并冲突

日期：2026-10-31。只分析已有运行，不修改实现、不发起模型请求。

运行：`runs/20261031-060945-deepseek-flash`；任务 terminal-bench/fix-git，BasicAgent，token_limit=30000、max_steps=20。依据用户提供的 show 输出和本地 samples.jsonl 交叉核对。

## 证据

- 八步模型调用，累计输入 28,191、输出 1,841，总计 **30,032 tokens**。前八步合计用量依次为 520、1049、1583、3247、4234、5623、6631、7145。
- 第 2 步已从 reflog 看到目标悬空提交；第 3～6 步继续检查不可达提交、diff 与历史关系。调查方向有效，但工具输出进入上下文，后续反复发送；输入约占总用量 94%。
- 第 7 步创建 recovered-changes 分支并尝试 merge，_includes/about.md 发生内容冲突，_layouts/default.html 已进入待提交状态。
- 下一轮 generate 因已用 token 达到阈值被拒绝；没有后续冲突处理或完成提交。模型未触及 20 步上限，不能把 max_steps 加大当作针对性修复。
- 预算检查在新请求开始前执行，因此最后一次请求可以超过剩余预算；30,032 与阈值 30,000 的差异符合现有机制。
- 停止后评分现有容器状态：test_layout_file 通过、test_about_file 失败，reward=0，outcome=token_limit。没有证据表明是环境故障。

## 指标解释

最后一次 shell 中 merge 后使用分号接 `echo "EXIT: $?"; git status`。输出明确记录 merge 返回 1，但整段脚本最后的 git status 返回 0，因此工具 exit_code=0、failed_commands=0。后者表示整次 bash 的退出状态，不能证明其中所有子命令成功。

## 建议的下一次实验

可保持模型、题目、Agent、max_steps 与其他配置不变，仅将 token_limit 从 30000 提高到 60000，作为一次预算敏感性试验。保留原失败运行并使用新容器、真实新请求，不向模型泄漏本次发现的提交或解法。增加预算不保证成功；比较是否完成冲突处理、最终分数、调用数与输入用量。

若再失败，再依据新轨迹决定是否调整观察长度或 Agent 策略；不要同时改变多个变量。没有自动开始新的收费实验。
