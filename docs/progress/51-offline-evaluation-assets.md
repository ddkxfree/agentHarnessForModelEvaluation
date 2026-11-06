# 第 51 轮：题库、镜像和判分依赖提前缓存

日期：2026-11-05。用户要求提前下载资源，减少正式执行时临时联网失败。本轮没有收费模型或裁判调用。

## 完成范围

- Terminal-Bench固定5题：原始任务文件、参考解、测试脚本和5个digest固定镜像全部缓存。
- SWE-bench Lite固定HF版本缓存；pytest-dev__pytest-5227、django__django-11099两个amd64镜像全部下载。两题分别在断网评分容器中应用参考补丁、执行官方测试并resolved。
- JobBench main／easy共128任务、981源文件、约623MB完整下载到本地；与固定HF文件目录逐项比对，记录每个文件SHA-256。版本d43a66575635da0d3f6cb9a20976dc1a60f3ffc9。
- 5个TB派生判分镜像预装Python3.13、uv0.9.5、pytest8.4.1、pytest-json-ctrf0.3.5；分别通过无网络依赖启动。Native五题Oracle 5/5通过、no-op 5/5失败。
- Harbor fix-git派生环境补齐tmux/asciinema，保留原任务原件，生成本地内容指纹。断网本地Oracle reward=1，无残留本Job容器。
- JobBench完整运行镜像按固定官方模板构建（系统包源改为官方HTTPS以解决HTTP连接失败），断网导入文档／表格／PDF／数据库包及LibreOffice和OCR检查通过。
- JobBench裁判依赖镜像单独venv安装固定requirements，断网导入和官方judge.py --help通过。没有裁判API调用。
- JobBench官方锁定的Harbor0.22.0安装于独立缓存目录venv，现有项目Harbor0.25.0保持独立。

本地readiness.json记录15个原始／派生镜像及12条Native/SWE离线验证；Harbor离线结果另存。文件与日志在 runs/offline-assets-20261105/，镜像保存在Docker存储中，未重复导出大tar包。

## 代码与后续使用

新增scripts/prepare_eval_assets.py：有界、可重复的7个任务原始资源准备入口，记录身份和失败状态，复制后验证TB指纹。

Native TB新增显式offline_verifier参数。默认仍保留原流程；启用时选择依赖预装的派生镜像，保持test_outputs.py，使用离线uvx替代联网bootstrap。scorer.describe记录模式、工具版本及命令，任务元数据保留原镜像身份。缺少或无效CTRF、pytest未正常启动归环境故障，CTRF作为产物保存。

Harbor用明确标记的派生本地任务和新指纹，不能当成未经修改的官方环境。完整使用说明见[资源说明](../offline-assets.md)。新题仍需补缓存，模型／裁判API和实时网页仍依赖网络。

## 验证

- 新增判分定向测试后，test_terminal_bench.py：23 passed。
- 非Docker回归：463 passed、8 skipped。首次受沙盒ps权限影响出现10项Harbor失败，正常本机权限重跑全部通过；不将权限失败藏作通过。
- Ruff check与format --check全仓通过，git diff --check通过。
- 真实断网检查：TB Oracle 5通过/no-op5失败，SWE2通过，Harbor本地派生Oracle1通过；JobBench运行与裁判模块导入通过。
- 原始容器或其他项目资源没有清理；只清理本轮自建临时容器，没有全局prune。

## 费用口径纠正

用户账户显示约0.2元且不是全部在今晚产生。此前1.387元是刻意高估的预算计提，不能写成真实消费。本批实际费用仍需供应商请求账单归属；已纠正文档与本地报告。
