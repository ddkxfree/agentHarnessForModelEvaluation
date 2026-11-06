# 评测资源本地缓存

本轮日期：2026-11-05。目标是将模型执行前的数据／镜像准备与运行时判分依赖提前准备，减少正式实验中因临时下载而失败。预下载和参考解验证不调用收费模型。

## 固定范围与入口

```bash
.venv/bin/python scripts/prepare_eval_assets.py --images
```

默认保存到 `runs/offline-assets-20261105/`，也可用 `--output-dir` 指定目录。清单记录每项成功／失败、题目指纹、镜像 ID、RepoDigest 和架构。单镜像拉取上限600秒，单次镜像批次等待总上限25分钟，重复运行优先复用 Docker 缓存。

| 类别 | 范围 | 版本 |
|---|---|---|
| Terminal-Bench | fix-git、log-summary-date-ranges、regex-log、openssl-selfsigned-cert、sqlite-db-truncate | 本项目固定 TB2.0 commit及各镜像digest |
| SWE-bench Lite | pytest-dev__pytest-5227、django__django-11099 | HF revision b0dde1093fe417d83b7184254edf8199c1f0dff5；镜像ID／digest记入清单 |
| JobBench | main／easy全部公开任务文件 | HF revision d43a66575635da0d3f6cb9a20976dc1a60f3ffc9；官方代码660b33a86ed032559df0b2fdc1ad38cf7ceb83f3 |

不是所有 SWE-bench 或所有 Harbor 数据集的完整镜像集合。新选任务时应补入清单。不要运行全局 Docker prune；这些缓存可能被其他项目共享。

## 已完成的实际离线检查

SWE两题分别在网络关闭的新评分容器中应用参考补丁并运行官方评测脚本，均 resolved。原始 patch／apply.log／eval.log／评分存于 `offline-checks/`，HF离线模式开启。pytest单题3项FAIL_TO_PASS与34项PASS_TO_PASS通过。这证明所选版本的环境与参考补丁判分链路可离线使用，不代表模型能力成绩。

## 判分依赖与派生镜像

Terminal-Bench的tests/test.sh虽然已缓存，仍动态执行apt、curl安装uv、uvx下载Python／pytest。不能因脚本在本地就宣称离线就绪。

预装依赖镜像与原始镜像分别标记，不覆盖官方tag或修改固定题目。其用途、依赖版本与镜像digest需要记录；若实际使用离线替代启动脚本，要在scorer.describe中记录命令和偏差，先验证参考解通过、不操作失败。原始脚本保持原件。

JobBench共享运行镜像按固定官方模板安装工具与包，不含题目、rubric或候选代码。Judge的Python包与运行环境不同，需要独立准备；裁判推理仍需外部API和对应凭据。

构建日志、原始Dockerfile与requirements文件留在本地缓存目录，成功构建才能标记ready，进行中或失败不得计作完成。

## 费用口径纠正

此前报告约1.387元为高峰价格、输入不计缓存优惠并按8.5元／美元计提的保守额度估算。用户实际账户反馈约0.2元，且并非全部属于今晚。无法据此归属本批真实费用，本批actual_cost保持未知，不能把预算估算写成账单。

## 仍需要网络的环节

模型和LLM裁判API、任务明确要求的实时网页，以及新版本／新题目的首次下载仍需要网络。本地缓存只覆盖已准备并检查过的固定资源。离线就绪应分为数据、镜像、运行依赖、判分验证四项分别记录。

## Native执行时显式选择离线判分

默认运行保持原始判分启动流程。使用预装依赖派生镜像时，明确开启：

```bash
.venv/bin/harness run -m none/none -t terminal-bench \
  -p 'terminal-bench.ids=[fix-git]' \
  -p terminal-bench.solver=oracle \
  -p terminal-bench.offline_verifier=true
```

该模式不改动固定题目的test_outputs.py，使用固定Python／uv／pytest和预缓存依赖代替联网安装步骤。scorer.describe记录offline标志、工具版本及命令，任务元数据保留原始镜像身份，实际使用镜像由sandbox记录。没有CTRF结果或pytest不能正常启动时归env_error，避免依赖失败被当成模型答错。CTRF原文作为产物保留。

Harbor使用本地派生任务，原始任务和原始tag不覆盖。离线Oracle配置位于 `runs/offline-assets-20261105/harbor-offline-oracle.yaml`，其中记录了新内容指纹与独立镜像。它是明确的环境派生版本，不能称为未经修改的官方任务。

本轮完成验证：Native五题Oracle 5/5通过，no-op 5/5失败；SWE两题参考补丁均resolved。整个验证没有调用收费模型。JobBench源文件列表与固定HF版本逐项比对，981个文件全部匹配并保存SHA-256清单。
