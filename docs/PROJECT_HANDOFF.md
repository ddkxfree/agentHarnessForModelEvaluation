# 项目交接文档（供架构评审）

- 基准提交：`master` 上的 `03a3ff0c6ad4e2608622488baebe315656c2dcbd`（2026-10-16 20:05 北京时间，PR #3 合并）
- 本文所有测试结果在 2026-10-24 于全新虚拟机上重新运行得到（Python 3.12.3、uv 0.13.5、Docker 29.1.3）
- 写作原则：只陈述有源码或测试证据的事实；没有验证过的内容标注 **【未验证】**；证据给出文件路径和行号

> **更新（第五阶段加固第一轮，见 `docs/progress/04-correctness-diagnostics.md`）**
>
> - 已修复：5.1 第 1、2 条；NaN 分数和 NaN/Infinity 被当作数字的问题；大整数精度问题；基线 Agent 的非法提交问题。
> - outcome 新增 `solver_error` 和 `scorer_error`，`ErrorKind` 新增 `solver` 和 `scorer`（4.1 节的定义已随之过时，以源码为准）。
> - 测试数量从 83 增加到 117。
> - 第 6 节的任务已按评审意见重排为五轮。

## 0. 一句话现状

路线图八个阶段中，阶段 1～4 已完成；阶段 5 的 8 步完成了前 7 步，第 8 步（真实 Terminal-Bench）未开始；阶段 6～8 未开始。共 83 个测试，在有 Docker 且已拉取 SWE-bench 镜像的环境中全部通过；ruff 检查和格式检查通过。

```text
uv run pytest -v                      → 77 passed, 6 skipped（无 Docker 时）
uv run pytest tests/test_docker_sandbox.py -v → 6 passed in 14.46s（Docker + pytest-5227 镜像）
uv run ruff check . && uv run ruff format --check . → All checks passed / 55 files already formatted
测试结束后带 harness.sandbox 标签的残留容器数：0
```

## 1. 目录结构与模块职责

```text
src/harness/
├── cli.py                 typer 命令：run / show / rescore / diff / list-tasks / sandbox-cleanup（272 行）
├── core/                  平台内核，新增评测集不应修改这里
│   ├── types.py           数据模型：Sample、GenerateConfig、ModelOutput、ToolCall、Step、SolveResult、Score、Outcome、SampleResult、classify()
│   ├── model.py           Model 抽象、OpenAICompatibleModel、MockModel、PROVIDERS、get_model()
│   ├── solver.py          Generate / Solver 协议，PromptSolver
│   ├── scorer.py          Scorer / SandboxScorer 协议，NumberMatchScorer、ChoiceScorer
│   ├── task.py            Task、DatasetSpec、注册表 register/get_task、确定性抽样 select()
│   ├── runner.py          并发、重试、超时、缓存、沙箱生命周期、异常归类、落盘、manifest
│   ├── cache.py           cache_key() 和 DiskCache（内容寻址、原子写）
│   ├── manifest.py        environment_info()、task_entry()
│   ├── fingerprint.py     对象和目录的内容指纹
│   ├── retry.py           retry_async（指数退避）
│   ├── config.py          YAML 运行配置 RunConfig / TaskConfig
│   └── io.py              JSON / JSONL 读写
├── sandbox/
│   ├── base.py            SandboxError、SandboxSpec、ExecResult、Sandbox、SandboxProvider
│   └── docker.py          DockerSandbox、DockerProvider（docker SDK）
├── solvers/
│   ├── basic_agent.py     基线工具调用 Agent（bash + submit）
│   ├── mini_swe_agent.py  mini-swe-agent 桥接（可选依赖）
│   └── oracle.py          运行终端任务的参考答案
├── tasks/
│   ├── gsm8k.py、mmlu.py  问答类评测集
│   ├── terminal.py        Terminal-Bench 风格任务加载器与 TestScriptScorer
│   ├── terminal_data/     3 个自建任务：hello-file、count-errors、fix-mean
│   ├── swebench.py        SWE-bench Lite 任务、SweBenchScorer、GoldPatchSolver（可选依赖）
│   └── _hf.py             HuggingFace 数据集加载
└── analysis/
    ├── stats.py           汇总、bootstrap 置信区间、Agent 统计
    ├── rescore.py         离线重打分
    └── diff.py            两次运行的 manifest 差异和逐样本翻转
tests/                     10 个文件，83 个测试；fakes.py 提供假沙箱和脚本化模型
configs/                   example、ollama-smoke、terminal-ollama、swebench-ollama
docs/                      roadmap.md、progress/01～03、hands-on.md、本文
```

依赖（`pyproject.toml`）：必选 datasets、docker、numpy、openai、pydantic、pyyaml、rich、typer；可选组 `swebench` = `mini-swe-agent>=2.4,<3`、`swebench>=5.0,<6`（锁定版本为 mini-swe-agent 2.4.6、swebench 5.0.2）。

## 2. 阶段 1～4：已实现功能与对应测试

| 功能 | 实现 | 测试证据 |
|---|---|---|
| GSM8K（固定 revision `740312a…`、few-shot、`####` 解析标准答案） | `tasks/gsm8k.py` | `test_tasks.py::test_gsm8k_parse_answer_strips_calculator_annotations`、`test_gsm8k_fewshot_messages` |
| MMLU（`cais/mmlu` all，revision `c30699e…`） | `tasks/mmlu.py` | `test_tasks.py::test_mmlu_row_to_prompt` |
| 确定性抽样、保持原顺序 | `core/task.py::select` | `test_tasks.py::test_select_is_deterministic_and_order_preserving` |
| 数字答案提取：`answer_is` → `hash_marker` → `boxed` → 兜底 `last_number`，记录命中规则 | `core/scorer.py:55-98` | `test_scorer.py` 中 `test_number_extraction` 6 例、`test_number_scorer_no_fallback_reports_extract_failure` |
| 选项提取：`answer_is` → `only_letter` → `leading_letter` → 兜底 `last_letter` | `core/scorer.py:101-141` | `test_scorer.py::test_choice_extraction` 8 例 |
| 并发上限、可重试错误重试、不可重试立即失败、重试次数有界、超时重试 | `core/runner.py`、`core/retry.py` | `test_runner.py` 中 5 个对应测试 |
| 请求缓存、中断后续跑、缓存键随生成参数和工具变化 | `core/cache.py` | `test_runner.py::test_cache_makes_reruns_free`、`test_cache_resumes_after_partial_failure`、`test_cache_key_depends_on_generate_config`；`test_agent.py::test_cache_key_depends_on_tools` |
| outcome 分类 | `core/types.py:119-132` | `test_runner.py::test_outcomes_are_classified` |
| manifest（模型、参数、数据集 revision、solver/scorer 指纹、git commit、依赖版本、uv.lock 哈希），不含 API key | `core/manifest.py`、`core/runner.py:128-199` | `test_run_artifacts.py::test_run_writes_manifest_summary_and_samples`、`test_manifest_contains_no_api_key` |
| 离线重打分 | `analysis/rescore.py` | `test_run_artifacts.py::test_rescore_uses_saved_outputs` |
| 运行对比 diff | `analysis/diff.py` | `test_run_artifacts.py` 中 4 个 diff 相关测试 |
| bootstrap 置信区间、错误计入分母 | `analysis/stats.py` | `test_run_artifacts.py::test_bootstrap_ci_brackets_mean`、`test_summary_counts_errors_as_failures` |

真实运行记录（来自 `docs/progress/01-platform-core.md`，2026-10-16，本次未重跑）：Ollama `qwen2.5:0.5b-instruct` 在 GSM8K 上 9/20，MMLU 上 17/40。

【未验证】`dashscope`、`deepseek`、`openai`、`vllm` 四个 provider 只有配置项，从未用真实 API key 调用过；真实调用只验证过 `ollama`。

## 3. 阶段 5：进度

| 步骤 | 状态 | 证据 |
|---|---|---|
| 1. Sandbox 抽象与 Docker 实现 | 完成 | `test_docker_sandbox.py::test_exec_files_timeout_and_isolation`（执行、文件读写、超时、断网、清理） |
| 2. Runner 管理沙箱生命周期，`env_error` | 完成 | `test_agent.py::test_sandbox_creation_failure_is_env_error`、`test_sandbox_is_closed_when_solver_raises`、`test_each_sample_gets_its_own_sandbox`；`test_docker_sandbox.py::test_missing_image_is_sandbox_error` |
| 3. 基线 Agent | 完成 | `test_agent.py` 16 个测试 |
| 4. 轨迹记录与 `harness show --sample` | 完成 | `test_agent.py::test_agent_runs_tools_in_sandbox_then_submits`、`test_agent_summary_stats` |
| 5. 3 个自建终端任务与 Oracle | 完成 | `test_docker_sandbox.py::test_oracle_solves_every_builtin_terminal_task`、`test_untouched_environment_fails_every_builtin_terminal_task` |
| **6. SWE-bench Lite** | **完成** | 见 3.1 |
| **7. mini-swe-agent 作为 Solver** | **完成** | 见 3.2 |
| 8. 真实 Terminal-Bench / Harbor 任务 | **未开始** | 无代码 |

### 3.1 SWE-bench Lite（`tasks/swebench.py`）

- 数据集 `SWE-bench/SWE-bench_Lite`，split `test`，revision `b0dde1093fe417d83b7184254edf8199c1f0dff5`。
- 每题一个官方镜像容器（`row["image"]`），工作目录 `/testbed`，**开网络**，4g 内存，2 CPU（`swebench.py:216-220`）。
- 判分流程（`SweBenchScorer.__call__`）：取补丁（输出是 diff 就用它，否则在容器里 `git add -A && git diff --cached HEAD`）；然后 `git reset --hard` 和 `git clean -fd`，依次尝试 `git apply`、`git apply --reject`、`patch --fuzz=5`；接着用 `make_test_spec(row).eval_script` 运行官方评测脚本，输出重定向到 `/tmp/eval.log`；最后用官方 `get_eval_report` 判定。
- `GoldPatchSolver` 应用参考补丁作为 Oracle。
- 测试：`test_swebench.py` 6 个单元测试；`test_docker_sandbox.py` 中参考补丁判为 `resolved`、什么都不改判为 `empty_patch`（2026-10-24 重跑通过，仅 pytest-5227）。
- 两题 Oracle 实测（来自 `docs/progress/03`，2026-10-16）：django-11099 FAIL_TO_PASS 3/3、PASS_TO_PASS 19/19；pytest-5227 3/3、34/34。【本次未重跑 django-11099，镜像未拉取】

### 3.2 mini-swe-agent 桥接（`solvers/mini_swe_agent.py`）

- 在工作线程中运行未修改的 `minisweagent.agents.default.DefaultAgent`。
- `HarnessModel.query` 通过 `asyncio.run_coroutine_threadsafe` 调用平台的 `generate`，因此缓存、重试、超时照常生效。
- `SandboxEnvironment.execute` 调用平台沙箱的 `exec`，提交判定逻辑与 mini-swe-agent 原版一致：首行是 `COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT` 且退出码为 0。
- 使用其自带配置：SWE-bench 用 `swebench.yaml`，终端任务用 `mini.yaml`；步数上限被覆盖，`cost_limit=0`。
- 测试：`test_mini_swe_agent.py` 8 个测试，全部用假沙箱。

### 3.3 阶段 5 实测结果（本地 CPU，`qwen2.5:3b-instruct`，2026-10-16）

| 任务 | Solver | 结果 |
|---|---|---|
| 3 个终端任务 | 基线 Agent | 1/3 |
| 3 个终端任务 | mini-swe-agent | 1/3（3 题都以 `RepeatedFormatError` 结束） |
| SWE-bench 2 题 | 基线 Agent | 0/2（第 1 步空回复） |
| SWE-bench 2 题 | mini-swe-agent | 0/2 |

这些数字只能说明链路可用，不能说明两种 Agent 的优劣：没有用更强的模型做过对比。【未验证】

## 4. 核心接口的真实定义

以下代码直接摘自源码。

### 4.1 数据模型（`core/types.py`）

```python
Message = dict[str, Any]  # OpenAI chat 格式

class Sample(BaseModel):
    id: str
    input: str
    target: str
    choices: list[str] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

class GenerateConfig(BaseModel):
    temperature: float = 0.0
    max_tokens: int = 1024
    top_p: float | None = None
    seed: int | None = None
    stop: list[str] | None = None
    timeout: float = 120.0
    def cache_fields(self) -> dict[str, Any]:  # 排除 timeout

class ModelOutput(BaseModel):
    text: str
    tool_calls: list[ToolCall] = []
    finish_reason: str | None = None
    usage: Usage = Usage()
    latency_s: float = 0.0
    cached: bool = False

class Step(BaseModel):
    index: int
    text: str
    tool_calls: list[ToolCall] = []
    observations: list[str] = []
    exit_codes: list[int | None] = []
    usage: Usage = Usage()
    latency_s: float = 0.0
    tool_latency_s: float = 0.0

class SolveResult(BaseModel):
    messages: list[Message]
    output: ModelOutput
    steps: list[Step] = []

class Score(BaseModel):
    value: float
    extracted: str | None = None
    method: str | None = None
    details: dict[str, Any] = {}

class Outcome(StrEnum):  # correct, wrong, extract_failed, truncated, step_limit, api_error, env_error
class ErrorKind(StrEnum):  # model, env

class SampleResult(BaseModel):
    sample: Sample
    messages: list[Message] = []
    output: ModelOutput | None = None
    steps: list[Step] = []
    score: Score | None = None
    error: str | None = None
    error_kind: ErrorKind | None = None
    outcome: Outcome | None = None
    attempts: int = 0
```

`classify()` 的判定顺序（`types.py:119-132`）：ENV → 有异常、没有输出或没有打分（记为 API_ERROR）→ `value >= 1` 为 CORRECT → `finish_reason == "length"` 为 TRUNCATED → `"max_steps"` 为 STEP_LIMIT → `extracted is None` 为 EXTRACT_FAILED → WRONG。

### 4.2 Task（`core/task.py`）

```python
class DatasetSpec(BaseModel):
    path: str
    config: str | None = None
    split: str
    revision: str | None = None

@dataclass
class Task:
    name: str
    dataset: DatasetSpec
    load: Callable[[], list[Sample]]
    solver: Solver
    scorer: Scorer | SandboxScorer
    params: dict[str, Any] = field(default_factory=dict)
    sandbox: Callable[[Sample], SandboxSpec] | None = None  # 设置后每个样本一个沙箱

def register(name: str) -> Callable[[TaskFactory], TaskFactory]
def get_task(name: str, **params) -> Task
def select(samples, limit, seed) -> list[Sample]
```

已注册的任务：`gsm8k`、`mmlu`、`terminal`、`swebench`。

### 4.3 Solver（`core/solver.py`）

```python
class Generate(Protocol):
    async def __call__(self, messages: list[Message], tools: list[dict] | None = None) -> ModelOutput: ...

class Solver(Protocol):
    name: str
    async def __call__(self, sample: Sample, generate: Generate, sandbox: Sandbox | None = None) -> SolveResult: ...
    def describe(self) -> dict[str, Any]: ...
```

已有实现：`PromptSolver`、`BasicAgent`、`MiniSweAgent`、`OracleSolver`、`GoldPatchSolver`。

### 4.4 Scorer（`core/scorer.py`）

```python
class Scorer(Protocol):          # 只依赖原始输出，可离线重打分
    name: str
    def __call__(self, sample: Sample, text: str) -> Score: ...
    def describe(self) -> dict[str, Any]: ...

class SandboxScorer(Protocol):   # 在沙箱里判分，不能离线重打分
    name: str
    requires_sandbox: bool
    async def __call__(self, sample: Sample, text: str, sandbox: Sandbox) -> Score: ...
    def describe(self) -> dict[str, Any]: ...
```

已有实现：`NumberMatchScorer`、`ChoiceScorer`、`TestScriptScorer`（SandboxScorer）、`SweBenchScorer`（SandboxScorer）。

### 4.5 模型（`core/model.py`）

代码中没有名为 `ModelProvider` 的类。"provider"是 `PROVIDERS` 字典加上 `get_model()` 工厂。

```python
class Model(ABC):
    name: str
    async def generate(self, messages: list[Message], config: GenerateConfig, tools: list[Tool] | None = None) -> ModelOutput
    def is_retryable(self, exc: BaseException) -> bool   # 默认只认 RetryableError
    def spec(self) -> dict[str, Any]                     # 用于 manifest 和缓存键，不能含密钥

PROVIDERS = {  # provider -> (默认 base_url, API key 环境变量, 兜底 key)
    "openai": (None, "OPENAI_API_KEY", None),
    "dashscope": ("https://dashscope.aliyuncs.com/compatible-mode/v1", "DASHSCOPE_API_KEY", None),
    "deepseek": ("https://api.deepseek.com", "DEEPSEEK_API_KEY", None),
    "ollama": ("http://localhost:11434/v1", None, "ollama"),
    "vllm": ("http://localhost:8000/v1", None, "EMPTY"),
}
def get_model(spec: str, *, base_url=None, api_key=None) -> Model   # "provider/model"；"none/none" 是占位
```

实现：`OpenAICompatibleModel`（`AsyncOpenAI`，`max_retries=0`，由 runner 统一重试；`spec()` 返回 type、provider、name、base_url）和 `MockModel`。

### 4.6 Sandbox（`sandbox/base.py`）

```python
class SandboxError(Exception): ...   # 环境故障，记为 env_error

class SandboxSpec(BaseModel):
    image: str | None = None
    build_context: Path | None = None
    workdir: str = "/app"
    network: bool = False
    memory: str = "1g"
    cpus: float = 1.0

class ExecResult(BaseModel):
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool = False
    def combined(self, limit: int | None = None) -> str   # 超长时截掉中间

class Sandbox(ABC):
    async def exec(self, cmd: str, *, timeout: float = 60.0) -> ExecResult
    async def write_file(self, path: str, content: bytes | str) -> None
    async def read_file(self, path: str) -> bytes
    async def copy_dir(self, src: Path, dest: str) -> None   # 默认实现：逐个 write_file
    async def close(self) -> None

class SandboxProvider(ABC):
    name: str
    async def create(self, spec: SandboxSpec, *, labels: dict[str, str]) -> Sandbox
    def describe(self) -> dict[str, Any]   # 默认只返回 {"name": ...}
```

Docker 实现要点（`sandbox/docker.py`）：

- 容器运行 `sleep infinity`，带 `init=True`；网络为 `none` 或 `bridge`；限制内存和 CPU；带标签 `harness.sandbox=1`。
- 命令以 `timeout -k 5 N bash -lc <cmd>` 执行，退出码 124 或 137 视为超时。
- 文件通过 `put_archive` 写入。
- 用 `build_context` 构建的镜像，标签是构建目录的内容指纹。

## 5. 技术债、已知 Bug 与架构风险

每条都附有本次（2026-10-24）复现的证据。

### 5.1 已确认的 Bug

1. 【已修复，见 04】**CI 从未在 `master` 上运行**。`.github/workflows/ci.yml` 的触发条件是 `push: branches: [main]`，而默认分支是 `master`。`gh run list --branch master` 返回空列表；CI 只在两个 PR 分支上跑过，结果均为 success。
2. 【已修复，见 04】**Scorer 内部的 bug 被记为模型错误**。`runner.py:96-100` 捕获 solver 和 scorer 的全部异常，除 `SandboxError` 外都标为 `ErrorKind.MODEL`。复现：让 scorer 抛出 `KeyError`，结果是 `outcome=api_error, error_kind=model`。评分器故障和 Agent 逻辑故障因此都会被算成模型失败。
3. **中文答案会被兜底规则提取错**。`NumberMatchScorer` 的规则只认英文的 `answer is`。复现：
   - `"最终答案是 42。验证过程一共用了 3 步。"` → `('3', 'last_number')`
   - `"<final>42</final> took 3 steps"` → `('3', 'last_number')`
   - 英文 `"The answer is 42. It took 3 steps."` → `('42', 'answer_is')`，正确。
   - 实际影响：现有 prompt 要求英文格式，兜底命中会记录在 `extraction_methods` 里，所以能被统计到，但仍会计为 wrong，不会计为 extract_failed。
4. **选项兜底规则会提取错**。复现：`"B and D are both wrong; the answer is clearly not A"` → `('A', 'last_letter')`；`"答案是 C"` 只能靠兜底规则命中（`last_letter`）。

### 5.2 可复现性缺口

5. **temperature > 0 时缓存仍默认开启**。缓存键只包含模型 spec、messages、生成参数（不含 timeout）和 tools；没有轮次或采样编号。复现：两个 `temperature=0.8` 的相同请求，缓存键相同。重复采样实验会全部命中缓存，只有加 `--no-cache` 才能避免。
6. **沙箱镜像版本未记录**。`DockerProvider.describe()` 只返回 `{"name": "docker"}`，manifest 里没有镜像 digest。实证：3 个终端任务都基于浮动标签 `FROM python:3.12-slim`，它的镜像 ID 在 2026-10-16 是 `a6e34c598f24`，2026-10-24 重新拉取后是 `a0f57064cbdc`。SWE-bench 镜像也是 `:latest` 标签。
7. **未记录服务端实际返回的模型版本**。`OpenAICompatibleModel.generate` 没有保存响应里的 `model` 和 `system_fingerprint` 字段。
8. **manifest 只跟踪 4 个包的版本**（openai、datasets、pydantic、numpy）。swebench 和 mini-swe-agent 的版本分别写在 scorer 和 solver 的指纹里，但 docker SDK、Docker 守护进程版本都没有记录。
9. **`rescore` 使用当前代码的 scorer**（`rescore.py:29`，`get_task(name, **params).scorer`），无法指定用某个历史版本的 scorer 重评；只会记录前后指纹的变化。

### 5.3 架构风险

10. **Solver 的产物只有一段文本**。`SolveResult.output.text` 是唯一的交付物。SWE-bench 的补丁要么来自这段文本，要么由 scorer 在容器里 `git diff` 得到；补丁只以截断到 20000 字符的形式保存在 `score.details.model_patch`（`swebench.py:134`）。因此：
    - 沙箱类任务（terminal、swebench）不能离线重评，`rescore` 会直接跳过（`rescore.py:30-32`）；
    - 将来要交付文件、文档类产物的任务，没有对应的数据结构。
11. **SWE-bench 在 Agent 用过的同一个容器里判分**，不是全新容器（scorer 指纹中记录了 `same_container: true`）。Agent 在 `/testbed` 之外做的改动会影响判分。
12. **SWE-bench 容器开着网络**，Agent 在整个解题过程中都能联网。理由是评测脚本里有 `pip install -e .`。【未验证：不开网络时 `pip install -e .` 是否真的失败】
13. **单个样本没有总时长上限**，只有单条命令超时和步数上限；CPU 上的小模型跑一道 SWE-bench 曾用时十几分钟。
14. **Sandbox 接口没有产物收集或快照能力**，例如 `collect_artifacts`、导出 diff、只读挂载。评分脚本是在 Agent 结束后才复制进容器的（`terminal.py:59`），但 SWE-bench 的隐藏测试补丁位于宿主机的 Python 进程中，并没有暴露给 Agent。
15. **没有模型能力矩阵**：例如是否支持工具调用、上下文长度。Ollama 的默认上下文长度是否会截断 SWE-bench 的长对话，【未验证】。
16. **SWE-bench 在任务构造和判分时依赖 HuggingFace 网络**（`swebench.py:58` 的 `_rows()`）。只有本机已缓存数据集时才能离线使用。

### 5.4 未解决的现象

17. 基线 Agent 通过 Ollama 0.40.5 调用 `qwen2.5:3b-instruct` 跑 SWE-bench 时，第 1 步返回 `content=""`、没有工具调用、`finish_reason=stop`，但 `output_tokens` 为 55～86。并发为 1 时也能复现。原因【未验证】。复现方法见 `docs/progress/03-swebench-mini-swe-agent.md`。

### 5.5 文档中的未验证内容

- `docs/hands-on.md` 中关于 Apple Silicon 上用 Rosetta 运行 x86_64 镜像、`OLLAMA_CONTEXT_LENGTH` 的建议：【未验证】，没有在 Mac 上实测过。

## 6. 下一步最小可执行任务与验收标准

按"先保证结果可信，再扩展功能"的顺序排列，每项都可以单独提交和评审。

| # | 任务 | 改动范围 | 验收标准 |
|---|---|---|---|
| T1 | CI 在 `master` 上触发 | `ci.yml` | push 到 `master` 后 `gh run list --branch master` 有记录，且结果为 success |
| T2 | 失败归类：新增 `scorer_error`（评分器异常），并与 `api_error` 区分 | `core/types.py`、`core/runner.py`、`analysis/stats.py` | 新增测试：scorer 抛异常时 outcome 为 `scorer_error`；模型调用失败仍为 `api_error`；summary 中有对应计数；现有 83 个测试不回退 |
| T3 | 采样实验不复用缓存：新增 `epochs` 或采样编号，并写进缓存键；temperature > 0 时默认不读缓存，或要求显式开启 | `core/cache.py`、`core/runner.py`、`cli.py` | 新增测试：T=0.8 时不同 epoch 的缓存键不同；同一 epoch 重跑能命中；manifest 记录缓存策略 |
| T4 | manifest 记录镜像 digest（每个样本实际使用的镜像 ID）、Docker 版本，以及服务端返回的 `model` 字段 | `sandbox/docker.py`、`core/model.py`、`core/manifest.py` | Docker 集成测试断言 manifest 或样本记录中含镜像 ID；单元测试断言 ModelOutput 保存了返回的 model 名称；`harness diff` 能显示镜像变化 |
| T5 | 产物与评分分离：SolveResult 增加 artifacts（如 `patch`），由 runner 从沙箱收集并完整落盘；SWE-bench 在**全新容器**中评分 | `core/types.py`、`core/runner.py`、`tasks/swebench.py`、`analysis/rescore.py` | 参考补丁和什么都不改两条 Docker 测试仍然通过；新增测试：对已保存的运行执行 `harness rescore`，SWE-bench 样本会在新容器里重评，而不是跳过 |
| T6 | 阶段 5 第 8 步：通过 Harbor 或其任务格式接入真实的 Terminal-Bench 任务（先接 3～5 个） | 新增 `tasks/<name>.py`，不改 `core/` | Oracle 全部通过、什么都不做的 Agent 全部失败（与现有约定一致）；`git diff` 显示 `core/` 无改动 |
| T7 | 中文和标签式答案提取：作为**新增规则**加入，旧规则保持不变，scorer 指纹随之变化 | `core/scorer.py` | 5.1 第 3、4 条中的样例有对应测试；对旧运行执行 `rescore` 会生成 `rescore_history`，能显示提取规则改动带来的分数变化 |

建议先做 T1、T2，因为改动最小，并且直接影响对结果的信任；然后做 T3、T4、T5，最后做 T6。

### 6.1 按评审意见调整后的顺序（当前执行计划）

| 轮次 | 内容 | 对应上表 | 状态 |
|---|---|---|---|
| 1 | 正确性与诊断：NaN 与数字比较、非法提交、失败分类、CI | T1、T2 | 已完成，见 `progress/04` |
| 2 | 失败可追踪：中途异常保留轨迹、完成样本及时落盘、清理失败要记录、单题总时长限制 | 5.3 第 13 条 | 未开始 |
| 3 | 独立复核：完整产物、环境身份、新容器评分、SWE-bench 不调模型重评；明确缓存复用策略 | T3（只做策略）、T4、T5 | 未开始 |
| 4 | 真实 Terminal-Bench | T6 | 未开始 |
| 5 | Solver 对照实验 | — | 未开始 |

T7（中文答案提取）和完整的 epochs 功能留到阶段 7。

## 7. 关键源码文件与提交

| 提交 | 时间（北京时间） | 内容 |
|---|---|---|
| `03a3ff0` | 2026-10-16 20:05 | PR #3：SWE-bench Lite、mini-swe-agent、可选依赖、文档 |
| `d454371` | 2026-10-16 18:47 | PR #2（squash 合并）：平台内核、沙箱、基线 Agent、终端任务 |
| `18fc529` | — | 仓库初始提交 |

评审时建议优先阅读：

- `src/harness/core/types.py`：数据模型和 outcome 分类
- `src/harness/core/runner.py`：执行编排、异常归类、沙箱生命周期
- `src/harness/core/scorer.py`、`src/harness/core/solver.py`、`src/harness/core/task.py`：三个核心协议
- `src/harness/core/cache.py`、`src/harness/core/manifest.py`：可复现性
- `src/harness/sandbox/base.py`、`src/harness/sandbox/docker.py`：沙箱
- `src/harness/tasks/swebench.py`、`src/harness/solvers/mini_swe_agent.py`：第一个真实的 Agent 评测集和第三方 Agent 接入
- `tests/fakes.py`、`tests/test_docker_sandbox.py`：测试替身和真实环境检查
- `docs/roadmap.md`、`docs/progress/`：计划和每轮记录
