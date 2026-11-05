import { agents, benchmarkById, models } from "../data/catalog";
import type { DraftInput } from "../types";

export const PARSE_EXAMPLES = [
  "用模型 A 跑 5 道终端题，每题 3 次，并发 2。",
  "用 qwen-plus 和 BasicAgent 跑 Terminal-Bench 全部 5 题，每题 1 次，并发 1，token 上限 30000。",
  "用 deepseek-flash 跑 GSM8K 演示子集，每题 1 次，并发 1。",
];

export type ParseResult =
  | { ok: true; draft: DraftInput; note: string }
  | { ok: false; message: string };

function fail(message: string): ParseResult {
  return {
    ok: false,
    message: `${message} 演示解析只接受这些例子：${PARSE_EXAMPLES.join(" ")}`,
  };
}

export function parseDemoInstruction(text: string): ParseResult {
  const source = text.trim().replace(/[。．.]+$/u, "");
  if (!source) return fail("没有输入。");
  if (/swe-?bench/i.test(source)) {
    return fail("SWE-bench 还没有接入这个原型，不能创建实验。");
  }

  let modelId: string | null = null;
  if (/模型\s*A/u.test(source)) modelId = "deepseek-flash";
  else if (/deepseek-flash/i.test(source)) modelId = "deepseek-flash";
  else if (/qwen-plus/i.test(source)) modelId = "qwen-plus";
  if (!modelId || !models.some((model) => model.id === modelId)) {
    return fail("没有识别到演示模型。可用模型 A、deepseek-flash 或 qwen-plus。");
  }

  let benchmarkId: string | null = null;
  if (/gsm8k/i.test(source)) benchmarkId = "gsm8k-demo";
  else if (/终端|terminal-bench/i.test(source)) benchmarkId = "terminal-bench";
  const benchmark = benchmarkId ? benchmarkById(benchmarkId) : undefined;
  if (!benchmark || !benchmark.canCreate) return fail("没有识别到可创建的演示评测集。");

  const repeatMatch = /每题\s*(\d+)\s*次/u.exec(source);
  const concurrencyMatch = /并发\s*(\d+)/u.exec(source);
  if (!repeatMatch || !concurrencyMatch) return fail("需要写明“每题 N 次”和“并发 N”。");
  const repeats = Number(repeatMatch[1]);
  const concurrency = Number(concurrencyMatch[1]);
  if (repeats < 1 || repeats > 5) return fail("重复次数只接受 1 到 5。");
  if (concurrency < 1 || concurrency > 4) return fail("并发只接受 1 到 4。");

  const tokenMatch = /token\s*上限\s*(\d+)/iu.exec(source);
  const maxTokens = tokenMatch ? Number(tokenMatch[1]) : null;

  const mentions = agents.filter((agent) => source.includes(agent.label));
  if (mentions.length > 1) return fail("请只写一个 Agent。");
  const agent = mentions[0] ?? agents.find((item) => item.id === "basic");
  if (!agent) return fail("没有可用的 Agent。");

  const all = /全部|演示子集/u.test(source);
  const countMatch = /(\d+)\s*(?:道|题)/u.exec(source);
  if (!all && !countMatch) return fail("请写明全部任务，或写明题数。");
  if (countMatch && Number(countMatch[1]) !== benchmark.tasks.length) {
    return fail(`${benchmark.name} 的演示题数是 ${benchmark.tasks.length}，不能解析成其他数量。`);
  }

  const model = models.find((item) => item.id === modelId);
  return {
    ok: true,
    note: "演示解析。结果已填入表单，提交前可以修改。这不是通用自然语言接口。",
    draft: {
      name: `${model?.label ?? modelId} · ${benchmark.name}`,
      benchmarkId: benchmark.id,
      taskIds: benchmark.tasks.map((task) => task.id),
      modelId,
      agentId: agent.id,
      runtimeId: agent.runtimeId,
      repeats,
      concurrency,
      maxTokens,
      maxSteps: null,
    },
  };
}
