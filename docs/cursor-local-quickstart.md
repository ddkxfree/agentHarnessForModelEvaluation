# 本机 Harbor＋Cursor CLI 快速开始

2026-11-06。执行结构：本机Python调度Harbor → 本机Docker容器运行Cursor CLI和题目 → Cursor服务端完成模型推理 → 本机保存日志并评分。不需要Cursor Cloud Agent，Mac需要保持运行和联网。

## 本机已验证

- Docker29.9.2，aarch64，分配10CPU、约12GB内存；Harbor0.25.0独立环境。
- 宿主Cursor CLI版本2026.08.11-e8db854；一次性题目容器内从官方安装地址安装CLI成功，版本2026.11.05-86047df。未升级宿主CLI。
- 复用`harness-harbor-offline:fix-git-v1`，实际为amd64镜像，在Mac上模拟运行。检查容器已自动删除；CLI会由Harbor在正式任务容器里重新安装，下载仍需联网，版本可能随安装源更新，应读取运行日志里的实际版本。
- 独立派生任务`runs/cursor-smoke-20261106/tasks/fix-git`仅将已有缓存任务的allow_internet改为true，原任务保持不变。指纹`1a233d5280c4430d`。使用预装判分依赖的派生环境，非未经修改的官方成绩；开放网络也需要在正式评测前按协议限制。
- 同一派生任务的Oracle参考解已通过，1/1 correct，退出0、无残留容器。记录位于`runs/cursor-smoke-20261106/results/fix-git-oracle-20261106T032841Z-4584`。
- 当前进程没有CURSOR_API_KEY。宿主`agent status`在受限执行环境报SecItemCopyMatching -50，不能据此判断编辑器账户未登录；容器适配仍需要明确注入用户Key。

## 用户认证与执行

从[Cursor Dashboard](https://cursor.com/dashboard)的API Keys页面创建用户Key；[官方CLI认证说明](https://cursor.com/docs/cli/reference/authentication)。不把Key写入YAML、命令参数或聊天。

在本机交互终端执行：

```bash
cd /Users/zhangde/PyCharmMiscProject/agentHarnessForModelEvaluation
.venv/bin/python scripts/run_cursor_smoke.py --list-models
```

脚本隐藏输入Key，仅传入子进程环境，不保存Key。模型列表请求不是作答请求。选择账户当前显示的Cursor Models型号；再执行（把MODEL_ID替换为确切ID）：

```bash
.venv/bin/python scripts/run_cursor_smoke.py --run --model MODEL_ID
```

未预先设置环境变量时会再次隐藏输入Key。执行一题、单并发、内部重试0、总执行上限900秒；准备和清理有额外独立期限。这里不是账户token或费用硬限额，运行前应核对账户按量付费设置。

模板`configs/harbor/fix-git-cursor-local.yaml`中的composer-2.5只是候选；脚本强制传入明确模型ID，不声称已验证账户支持它。启动时生成无密钥的唯一配置文件，使用现有harness监管、归档和资源清理，不在Cursor云端启动任务。

## 结果与剩余验收

每次run独立存于`runs/cursor-smoke-20261106/results/`。Harbor原始作答流通常为trial下`agent/cursor-cli.txt`，转换轨迹为`agent/trajectory.json`，评分在verifier和result.json中；任务中断可能缺失末尾事件或usage。详见`observability.md`。

环境安装和Oracle评分已验证；Cursor认证、账户模型列表、真实作答、扣费池、实际usage／轨迹兼容性仍待用户创建Key后运行。没有发起任何Cursor模型请求。此脚本依赖本机已准备且Git忽略的任务和venv，不是任意新机器的一键安装器。
