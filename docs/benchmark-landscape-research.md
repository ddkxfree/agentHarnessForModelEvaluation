# SWE-bench、Terminal-Bench、JobBench：题库、质量与缓存调研

调研日期：2026-11-05（北京时间）。本轮只读调研，没有新增模型调用、升级已有执行环境或下载新增评测镜像。数字对应特定发行版或官方网页，不把仓库main、历史论文、官网首页和正式release混为同一个版本。

## 1. 结论

- 三者都是评测集／评测体系；Harbor是组织任务与执行的框架，不是第四种题目类型。
- SWE-bench Verified与SWE-bench Pro不是“免费版／付费版”或同一平台两种会员。Verified是原SWE-bench项目的人审子集；Pro由Scale AI另行开发，另有版本和私有划分。
- 文本元数据、任务说明和固定脚本适合全量缓存；所有版本所有实例镜像不宜一次缓存。镜像应按下一批实际选题预热，按digest、平台和依赖层复用。
- 本项目已有的TB2.0与SWE Lite运行是有效的历史基线，但不能代表已经接入当前TB4.1或SWE Pro V2。
- 运行时对Agent隐藏的测试，不等于数据集由主办方私有持有。训练／测试划分也不自动表示公开／私有。

## 2. 规模与版本

### 2.1 SWE-bench家族

| 名称 | 任务数 | 口径 |
|---|---:|---|
| SWE-bench Full | 2,294 | 经典test split，不含训练／开发集 |
| SWE-bench Lite | 300 | test split，另有23个开发实例 |
| SWE-bench Verified | 500 | 人工筛选的原始SWE-bench子集 |
| Bash Only | 同一批Verified 500题 | 统一mini-SWE-agent的比较设置，不是另一套500题 |
| SWE-bench Multilingual | 300 | 官方介绍9种语言、42个仓库 |
| SWE-bench Multimodal V2 | 480 | 原版517；2026-09-01 V2保留480，公开测试集与工具 |
| SWE-bench Pro V1公开集 | 731 | Scale AI独立基准的旧公开版本 |
| SWE-bench Pro V2公开集 | 642 | 2026-09-22发布，HF默认配置；另有Hard-51子集 |

Full、Lite、Verified有子集和实例重叠，不能简单相加当成独立题量。Pro单独维护，不是Verified的付费升级。

依据：[SWE官方总览](https://www.swebench.com/)、[Lite](https://www.swebench.com/lite.html)、[Verified](https://www.swebench.com/verified.html)、[Multilingual](https://www.swebench.com/multilingual.html)、[Multimodal](https://www.swebench.com/multimodal.html)、[Pro官方数据卡](https://huggingface.co/datasets/ScaleAI/SWE-bench_Pro/blob/main/README.md)。

### 2.2 Pro的公开与私有

原始Pro论文总计1,865题，分为：公开731、商业私有276、held-out858。后两者不公开下载。商业私有题来自专有代码库，held-out使用与公开集不同的仓库，均不能当作已经可以在本机缓存的公开数据。

这些是V1论文的总构成，不能将V2公开642和旧私有数拼成一个未经主办方确认的新总数。V2公开集是新release，应单独存版本及题目清单。[原始Pro论文](https://static.scale.com/uploads/654197dc94d34f66c0f5184e/SWEAP_Eval_Scale%20(9).pdf)

经典Full／Lite／Verified为公开数据；Verified一词指人工核验，不指私有隐藏集。Pro有公开部分，并非整个Pro都私有。

### 2.3 Terminal-Bench

| 发行版 | 题数 | 状态 |
|---|---:|---|
| 2.0 | 89 | 本项目当前固定来源，从中接入5题 |
| 2.1 | 89 | 对2.0中的28题进行修复 |
| 3.0 | 74 | 官方首发说明为74题、7个领域 |
| 4.0 | 66 | 官网首页仍显示的主版本；按固定tag清单计数 |
| 4.1.0 | 66 | 官方最新release，2026-11-03 UTC发布；固定tag清单66项 |
| 4.1.0 CPU-only | 63 | 官方发布的CPU任务子集，不是额外63道新题 |

主分支、v4.0.0、v4.1.0清单均已实际读取并计数66，不依赖第三方排行榜数字。固定tag快照保存在runs/benchmark-research-20261105/。v4.1.0 commit为4070d6cef56a7d20cb5dc98d5e10ada88a6b9440。

当前公开发布说明、源码和Hub提供上述任务；在核查的官方材料中，没有找到一个能明确报告数量、单独提供商业评测的标准“Terminal-Bench私有split”。不据此断言主办方内部从未有未发布任务，也不把Hub支持私有数据集当作官方TB存在私有题库的证据。

依据：[2.1说明](https://www.tbench.ai/news/terminal-bench-2-1)、[3.0说明](https://www.tbench.ai/news/terminal-bench-3-0)、[4.0说明](https://www.tbench.ai/news/terminal-bench-4-0)、[4.1正式发布](https://github.com/harbor-framework/terminal-bench/releases/tag/v4.1.0)、[v4.0固定清单](https://raw.githubusercontent.com/harbor-framework/terminal-bench/v4.0.0/tasks/dataset.toml)、[v4.1固定清单](https://raw.githubusercontent.com/harbor-framework/terminal-bench/v4.1.0/tasks/dataset.toml)。

### 2.4 JobBench

当前官方评测仓库：main65题、easy63题，共128个任务目录。main是默认排行榜split，easy为简化任务，不应混入main一起算官方成绩；也不应将easy假定为独立泛化测试集。

论文摘要写130题，当前仓库和本机固定数据为128题。这是来源／版本口径差异，本次未查明两题差额的具体变更原因，不编造解释。[官方仓库](https://github.com/Job-Bench/job-bench-eval)、[论文](https://arxiv.org/abs/2605.26329)

公开材料包含工作文件、rubric等。当前核查材料没有给出单独私有JobBench题库的正式数量或访问路径，因此不声称存在可购买或可全量下载的私有集。模型作答时应隔离rubric和隐藏参考，它们公开可获取不代表可以提供给考生。

## 3. 题目质量与适用性

### SWE

优势是来源于真实仓库问题，有代码补丁与可执行测试。Verified的人审关注说明清晰、测试合理和可解性；Lite额外筛选更自包含的修复，降低开发成本。但测试通过只能证明满足已覆盖要求，公开问题还存在训练接触、检索到上游修复的风险，不能把Verified理解为永久无误或完全无污染。[Verified方法说明](https://www.swebench.com/verified.html)、[Lite筛选标准](https://www.swebench.com/lite.html)

Pro面向较长的软件工程任务，但“Pro”不是质量免检。V2剔除了89道旧题，修复依赖、说明及判分问题；协议限制Agent联网并在干净环境复评补丁。原官方宣传页与详细HF数据卡对说明改写数量有不同表述，本报告不合并引用这些细项；642公开题和51题Hard配置两者一致。质量判断必须绑定release和协议。[Scale V2发布](https://labs.scale.com/leaderboard/swe_bench_pro_public_v2)、[V2数据卡](https://huggingface.co/datasets/ScaleAI/SWE-bench_Pro/blob/main/README.md)

### Terminal-Bench

适合终端、系统、数据、科学计算等工具工作流。每题包含环境、说明、参考解和验证器，比单纯答案匹配能观察更多操作行为。

2.1官方修复了2.0的28题，涉及外部依赖漂移、资源不足和说明／测试不一致；这与本项目遇到的安装失败却写0分相呼应，但不能据此认定我们的具体问题必然已被新版本修复。4.0校准资源并删除饱和任务，4.1继续改进判分隔离、锁文件和防reward hacking。说明质量需要持续维护，不宜沿用旧版名义判断最新版。[2.1官方分析](https://www.tbench.ai/news/terminal-bench-2-1)、[4.0资源校准](https://www.tbench.ai/news/terminal-bench-4-0)、[4.1发布](https://github.com/harbor-framework/terminal-bench/releases/tag/v4.1.0)

4.0官方将任务Agent超时设为8小时。这是上限，并非每题一定跑8小时。4.1 CPU-only不等于低内存、低耗时或Mac原生兼容。部分任务依赖GPU、特殊系统能力或大资源，不应仅按题数决定能否本机全跑。

### JobBench

覆盖35类职业，考查多源材料交叉核对、矛盾识别及文件交付；适合观察长轨迹和DOCX／XLSX／CSV／数据库等产物。论文描述每题平均35.6个二元rubric标准，官方方法强调事实依据。[论文](https://arxiv.org/abs/2605.26329)、[方法介绍](https://job-bench.github.io/)

结果依赖文件提取、工具能力、外部网页以及LLM裁判。当前官方默认Grok4.3；rubric分块要求块内条件全部满足才取得该块权重，任务分数归一化后再对任务求平均。它不是普通单测成功率，换裁判或提取器必须记录为不同设置。[评分和覆盖率规则](https://github.com/Job-Bench/job-bench-eval)

本项目的离线标准库试跑缺少外部参考与官方Judge，不能与官方main成绩直接比较；已完成的完整依赖缓存有助于消除部分工具差异，仍不等于接入和评分全部验收。

## 4. 每一道SWE题都需要一个镜像吗

更准确地说，每道题需要与其仓库、base commit和依赖匹配的可复现实例环境。官方Docker harness通常提供／构建实例镜像，但不是每次作答都下载或构建一次，也不是每道题占一份完全独立的操作系统。

官方采用base、environment、instance三层：共享系统工具；共享某类依赖配置；最后叠加具体仓库实例。Docker可复用公共层。同一道题换模型或重复运行，可以复用镜像，每次新建独立容器；生成补丁后在干净评分容器中验证，避免Agent对环境的其他修改影响分数。[官方Docker架构](https://www.swebench.com/SWE-bench/guides/docker_setup/)、[评分流程](https://www.swebench.com/SWE-bench/guides/evaluation/)

我们现在的Native SWE适配选择官方实例镜像并在新容器评分，当前两题镜像为amd64，本机arm64使用模拟。不同发行版、语言与实现可能有其他安排，不能把某个镜像策略说成所有SWE类项目的唯一机制。

官方资源指南给出至少120GB空间等准备建议，并估计instance级全缓存约2TB、env级约100GB；这些是指南的量级估计，不是对某个子集或本机的精确容量报价。不能拿两题大小乘题数当可靠预算。[官方缓存建议](https://www.swebench.com/SWE-bench/guides/docker_setup/)

## 5. 是否需要全量缓存

| 资源 | 建议 | 理由 |
|---|---|---|
| 公开题目元数据、版本清单、说明与评分源码 | 全量固定版本缓存 | 比实例环境便宜，方便筛选和审计；评分资料须对Agent隔离 |
| JobBench全部材料 | 保留全量缓存 | 当前128题源文件约623MB，本机已完成 |
| SWE所有实例镜像 | 暂不全量 | 容量和构建成本大，历史版本还有重复，Mac架构也需核对 |
| SWE下一批20～50个实际选题镜像 | 提前下载并做正反例检查 | 直接服务批量实验，可测出实际共享层与磁盘增量 |
| Terminal-Bench当前版CPU-only元数据 | 建议全量缓存 | 63题规模适合建立能力与资源目录，但要验证Harbor版本兼容 |
| Terminal-Bench当前版全部镜像 | 按实际计划预热 | CPU-only仍可能高资源；先选一批再补齐 |
| Pro商业／held-out | 无法普通公开缓存 | 需要主办方授权或评测服务，不要当本机下载失败 |

缓存应区分metadata_cached、image_cached、runtime_ready、verifier_validated四项。镜像按digest＋architecture去重，题目版本和数据集成员关系单独建表，避免Full／Lite／Verified重叠导致重复统计。

## 6. 对本项目的建议

1. 保留当前TB2.0五题及离线派生环境，作为回归基线，不覆盖历史记录。
2. 新增TB4.1 CPU-only目录与能力／资源检查，独立环境核查其锁文件、Workers等是否兼容固定Harbor0.25.0；不直接升级现有入口。
3. SWE先选择一个主评测版本：工程调试可继续Lite；需要对照常见公开结果时增加Verified。公开文本可以全缓存，镜像先做20～50题资源实测。
4. Pro V2及Hard-51作为独立Task／数据集适配计划，不用改模型名称的方式冒充已经接入。
5. JobBench保留全量材料，从main分层选5～10题验收官方生成／提取／裁判流程，再扩大到65题；easy作为开发便利集单独报告。
6. 公开结果与真正私有保留集分别展示；实验要记录版本、task集合、Agent、模型、预算、判分协议和派生环境状态，不能只存一个“swe-bench”名字。

以上为调研后的实施建议，本轮没有自动下载新增版本镜像、升级运行环境或发起收费实验。
