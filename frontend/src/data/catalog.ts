import type { AgentDef, BenchmarkDef, ModelDef, RuntimeDef, TaskDef } from "../types";

const tb = (
  id: string,
  name: string,
  tags: string[],
  instruction: string,
  fingerprint: string,
): TaskDef => ({
  id,
  name,
  tags,
  instruction,
  fingerprint,
  requirements: "需要容器。本原型只展示任务说明，不拉取镜像。",
});

export const benchmarks: BenchmarkDef[] = [
  {
    id: "terminal-bench",
    name: "Terminal-Bench",
    version: "2.0 · 2fd12b88",
    summary:
      "仓库已固定的 5 道 Terminal-Bench 2.0 题，覆盖 git、日志、正则、证书和二进制恢复。这里的成绩是演示脚本。",
    compatibility:
      "兼容状态：演示。仓库中的 Harbor 固定为 0.25.0。本页不表示该版本已支持或已验收 Terminal-Bench 的全部最新功能。",
    canCreate: true,
    requirements: "Native 或 Harbor 执行环境。演示运行不启动 Docker。",
    tasks: [
      tb(
        "fix-git",
        "fix-git",
        ["git"],
        "仓库历史损坏。找到应当保留的提交并完成合并。不要改验证脚本。",
        "1b12832fdb57503e",
      ),
      tb(
        "log-summary-date-ranges",
        "log-summary-date-ranges",
        ["日志分析"],
        "按题目给出的日期区间汇总日志中的错误计数，把结果写到指定文件。",
        "ba620079eb149d4c",
      ),
      tb(
        "regex-log",
        "regex-log",
        ["正则"],
        "用正则从访问日志抽出指定字段，输出格式以题目说明为准。",
        "5726420883fa170b",
      ),
      tb(
        "openssl-selfsigned-cert",
        "openssl-selfsigned-cert",
        ["安全工具"],
        "生成一份自签名证书，主体和有效期必须与题目一致。",
        "c9ef93da6532aa9b",
      ),
      tb(
        "sqlite-db-truncate",
        "sqlite-db-truncate",
        ["二进制恢复"],
        "SQLite 文件被截断。把仍能读出的记录导出，不要把环境故障记成答错。",
        "953079359d19534e",
      ),
    ],
  },
  {
    id: "gsm8k-demo",
    name: "GSM8K 演示子集",
    version: "自拟 4 题 · 非官方全文",
    summary: "四道自拟算术题，用来演示第二个评测集。不是 GSM8K 官方题目，也不能和终端题加总。",
    compatibility: "兼容状态：演示。未连接官方数据集，也没有真实模型作答。",
    canCreate: true,
    requirements: "只需要文本生成。演示运行不发起请求。",
    tasks: [
      {
        id: "gsm-pens",
        name: "文具找回",
        tags: ["算术"],
        instruction: "一支笔 3 元，买 4 支，付给店员 20 元。应找回多少元？",
        fingerprint: "demo-gsm-pens",
        requirements: "文本题。",
      },
      {
        id: "gsm-shelves",
        name: "书架层数",
        tags: ["算术"],
        instruction: "每层放 8 本书，现有 36 本。装满后还剩几本？",
        fingerprint: "demo-gsm-shelves",
        requirements: "文本题。",
      },
      {
        id: "gsm-tickets",
        name: "门票",
        tags: ["算术"],
        instruction: "成人票 12 元，儿童票 7 元。2 张成人票和 3 张儿童票一共多少元？",
        fingerprint: "demo-gsm-tickets",
        requirements: "文本题。",
      },
      {
        id: "gsm-ribbon",
        name: "丝带",
        tags: ["算术"],
        instruction: "一根丝带长 90 厘米，剪成等长的 6 段。每段多少厘米？",
        fingerprint: "demo-gsm-ribbon",
        requirements: "文本题。",
      },
    ],
  },
  {
    id: "swebench",
    name: "SWE-bench Lite",
    version: "未接入原型",
    summary: "仓库的 Python 评测可以跑 SWE-bench。这个原型没有接入，下面只有题目名称，没有成绩。",
    compatibility: "兼容状态：未接入。不能创建实验，页面上没有填写分数。",
    canCreate: false,
    requirements: "真实运行需要 Docker 与官方镜像。原型不启动这些环境。",
    tasks: [
      {
        id: "django__django-11099",
        name: "django__django-11099",
        tags: ["软件工程"],
        instruction: "目录示例。原型没有轨迹，也没有分数。",
        fingerprint: "unconnected",
        requirements: "未接入。",
      },
      {
        id: "pytest-dev__pytest-5227",
        name: "pytest-dev__pytest-5227",
        tags: ["软件工程"],
        instruction: "目录示例。原型没有轨迹，也没有分数。",
        fingerprint: "unconnected",
        requirements: "未接入。",
      },
    ],
  },
];

export const models: ModelDef[] = [
  {
    id: "deepseek-flash",
    label: "deepseek-flash",
    alias: "模型 A",
    note: "演示名称。没有服务端返回的版本号，也没有调用日期。",
  },
  {
    id: "qwen-plus",
    label: "qwen-plus",
    alias: null,
    note: "演示名称。连接测试不会发送凭证。",
  },
];

export const agents: AgentDef[] = [
  {
    id: "basic",
    label: "BasicAgent",
    runtimeId: "native",
    note: "仓库里的基线 Solver。原型不运行它的循环。",
  },
  {
    id: "mini",
    label: "mini-swe-agent",
    runtimeId: "native",
    note: "可替换 Solver。原型不安装这项可选依赖。",
  },
  {
    id: "terminus-2",
    label: "Terminus-2",
    runtimeId: "harbor",
    note: "Harbor 侧的 Agent 名称。和 BasicAgent 的差异是整套系统差异，不是执行器单因素。",
  },
];

export const runtimes: RuntimeDef[] = [
  {
    id: "native",
    label: "Native",
    note: "Harness 自己的执行循环。原型里的运行状态是前端模拟的。",
  },
  {
    id: "harbor",
    label: "Harbor 0.25.0",
    note: "仓库固定的 Harbor 发行版。原型不安装 Harbor，也不启动 Docker。",
  },
];

export const PRICE = {
  date: "2026-11-05",
  inputPerMillionCny: 1,
  outputPerMillionCny: 4,
  note: "演示估价口径（2026-11-05）：未命中输入按 1 元/百万 tokens，输出按 4 元/百万 tokens。缓存已计入输入时不再加一遍。这不是实付账单。",
};

export function benchmarkById(id: string): BenchmarkDef | undefined {
  return benchmarks.find((item) => item.id === id);
}

export function taskById(taskId: string): TaskDef | undefined {
  for (const benchmark of benchmarks) {
    const task = benchmark.tasks.find((item) => item.id === taskId);
    if (task) return task;
  }
  return undefined;
}

export function modelById(id: string): ModelDef | undefined {
  return models.find((item) => item.id === id);
}

export function agentById(id: string): AgentDef | undefined {
  return agents.find((item) => item.id === id);
}

export function runtimeById(id: string): RuntimeDef | undefined {
  return runtimes.find((item) => item.id === id);
}
