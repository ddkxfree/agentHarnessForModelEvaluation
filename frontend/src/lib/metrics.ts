import { PRICE, taskById } from "../data/catalog";
import type { Experiment, Outcome, Sample, Usage } from "../types";

const MODEL_FAIL: Outcome = "wrong";
const TERMINAL = new Set<Outcome>([
  "correct",
  "wrong",
  "budget_exhausted",
  "env_error",
  "cancelled",
  "missing",
]);

export interface TokenTotals {
  modelSamples: number;
  reportedSamples: number;
  inputTokens: number | null;
  outputTokens: number | null;
  cachedTokens: number | null;
  cacheNote: string;
  costCny: number | null;
  costKind: "estimate" | "unknown" | "not_applicable";
  costNote: string;
}

export interface TimeTotals {
  wallClockS: number | null;
  taskSeconds: number | null;
  finishedWithTime: number;
  finishedMissingTime: number;
  unfinished: number;
  note: string;
}

export interface RunSummary {
  planned: number;
  correct: number;
  modelFailures: number;
  envErrors: number;
  budget: number;
  cancelled: number;
  missing: number;
  unfinished: number;
  progressed: number;
  successRate: number | null;
  incomplete: boolean;
  warning: string | null;
  tokens: TokenTotals;
  time: TimeTotals;
  steps: number;
  toolCalls: number;
}

export interface HeldOut {
  reason: "env" | "cancelled" | "budget" | "unfinished" | "missing" | "identity" | "unmatched";
  taskId: string;
  repeat: number;
  detail: string;
  leftOutcome: Outcome | null;
  rightOutcome: Outcome | null;
}

export interface PairItem {
  taskId: string;
  repeat: number;
  left: Sample;
  right: Sample;
}

export interface Comparison {
  sameBenchmark: boolean;
  notes: string[];
  configChanges: { key: string; left: string; right: string }[];
  buckets: {
    failSuccess: PairItem[];
    successFail: PairItem[];
    bothSuccess: PairItem[];
    bothFail: PairItem[];
  };
  held: HeldOut[];
  leftRate: number | null;
  rightRate: number | null;
  percentagePoints: number | null;
  tokensComparable: boolean;
  capability: {
    tag: string;
    leftCorrect: number;
    leftN: number;
    rightCorrect: number;
    rightN: number;
  }[];
}

function modelSamples(samples: Sample[]): Sample[] {
  return samples.filter((sample) => sample.usage.status !== "not_applicable");
}

function estimate(usage: Usage): number | null {
  if (usage.status !== "reported") return null;
  if (usage.inputTokens == null || usage.outputTokens == null) return null;
  return (
    (usage.inputTokens * PRICE.inputPerMillionCny +
      usage.outputTokens * PRICE.outputPerMillionCny) /
    1_000_000
  );
}

export function tokenTotals(samples: Sample[]): TokenTotals {
  const modeled = modelSamples(samples);
  if (modeled.length === 0) {
    return {
      modelSamples: 0,
      reportedSamples: 0,
      inputTokens: null,
      outputTokens: null,
      cachedTokens: null,
      cacheNote: "这些样本没有模型调用，token 不适用。",
      costCny: null,
      costKind: "not_applicable",
      costNote: "没有模型调用，费用不适用，不是 0 元。",
    };
  }
  const reported = modeled.filter((sample) => sample.usage.status === "reported");
  const complete = reported.length === modeled.length;
  const sum = (field: "inputTokens" | "outputTokens" | "cachedTokens"): number | null => {
    if (!complete) return null;
    if (reported.some((sample) => sample.usage[field] == null)) return null;
    return reported.reduce((total, sample) => total + (sample.usage[field] ?? 0), 0);
  };
  const flags = reported.map((sample) => sample.usage.cacheIncludedInInput);
  let cacheNote = "有样本未记录用量。合计不使用部分求和，也不把缺失当成 0。";
  if (complete) {
    if (flags.every((flag) => flag === true)) {
      cacheNote = "缓存已计入输入。合计不再把缓存加到输入上。";
    } else if (flags.every((flag) => flag === false)) {
      cacheNote = "记录说明缓存未计入输入。页面分开显示，不自动相加。";
    } else {
      cacheNote = "缓存是否已计入输入并不一致或未记录。页面不提供输入与缓存的和。";
    }
  }
  const costs = reported.map((sample) => estimate(sample.usage));
  const costOk = complete && costs.every((cost) => cost != null);
  return {
    modelSamples: modeled.length,
    reportedSamples: reported.length,
    inputTokens: sum("inputTokens"),
    outputTokens: sum("outputTokens"),
    cachedTokens: sum("cachedTokens"),
    cacheNote,
    costCny: costOk ? costs.reduce<number>((total, cost) => total + (cost ?? 0), 0) : null,
    costKind: costOk ? "estimate" : "unknown",
    costNote: costOk
      ? PRICE.note
      : "用量没有覆盖全部模型样本，费用不做部分估算，显示为未记录。",
  };
}

export function timeTotals(experiment: Experiment): TimeTotals {
  const unfinished = experiment.samples.filter(
    (sample) => sample.outcome === "pending" || sample.outcome === "running",
  );
  const finished = experiment.samples.filter((sample) => TERMINAL.has(sample.outcome));
  const missing = finished.filter((sample) => sample.wallTimeS == null);
  const seconds = finished.reduce((total, sample) => total + (sample.wallTimeS ?? 0), 0);
  let note = "墙钟时长包含排队和间隔，不等于各题耗时之和。";
  if (missing.length > 0) note = "已结束样本里有耗时缺失，所以不显示任务耗时合计，也不把缺失当成 0。";
  else if (unfinished.length > 0) note = `任务耗时只合计已结束的 ${finished.length} 题。未结束 ${unfinished.length} 题未计入。墙钟时长另计。`;
  return {
    wallClockS: experiment.wallClockS,
    taskSeconds: missing.length === 0 ? (finished.length > 0 ? seconds : null) : null,
    finishedWithTime: finished.length - missing.length,
    finishedMissingTime: missing.length,
    unfinished: unfinished.length,
    note,
  };
}

export function summarize(experiment: Experiment): RunSummary {
  const samples = experiment.samples;
  const count = (outcome: Outcome) => samples.filter((sample) => sample.outcome === outcome).length;
  const planned = samples.length;
  const correct = count("correct");
  const unfinished = count("pending") + count("running");
  const incomplete = unfinished > 0 || count("missing") > 0 || experiment.status === "running";
  let warning: string | null = null;
  if (experiment.status === "cancelled") {
    warning = "运行已取消。取消的样本不算模型失败。成功率的分母仍是计划样本，取消算未通过。";
  } else if (incomplete) {
    warning = "运行不完整。未结束和缺失的样本算未通过。只在已评分样本上算出的比例，不是这次运行的成绩。";
  }
  const tokens = tokenTotals(samples);
  return {
    planned,
    correct,
    modelFailures: count(MODEL_FAIL),
    envErrors: count("env_error"),
    budget: count("budget_exhausted"),
    cancelled: count("cancelled"),
    missing: count("missing"),
    unfinished,
    progressed: samples.filter((sample) =>
      ["correct", "wrong", "budget_exhausted", "env_error", "missing"].includes(sample.outcome),
    ).length,
    successRate: planned === 0 ? null : correct / planned,
    incomplete,
    warning,
    tokens,
    time: timeTotals(experiment),
    steps: samples.reduce((total, sample) => total + sample.steps.length, 0),
    toolCalls: samples.reduce(
      (total, sample) => total + sample.steps.filter((item) => item.kind === "tool_call").length,
      0,
    ),
  };
}

function configRows(experiment: Experiment): { key: string; value: string }[] {
  return [
    { key: "评测集", value: experiment.benchmarkId },
    { key: "版本", value: experiment.benchmarkVersion },
    { key: "任务", value: [...experiment.taskIds].sort().join(", ") },
    { key: "模型", value: experiment.modelId },
    { key: "Agent", value: experiment.agentId },
    { key: "执行环境", value: experiment.runtimeId },
    { key: "重复次数", value: String(experiment.repeats) },
    { key: "并发", value: String(experiment.concurrency) },
    { key: "token 上限", value: experiment.maxTokens == null ? "未设置" : String(experiment.maxTokens) },
    { key: "步数上限", value: experiment.maxSteps == null ? "未设置" : String(experiment.maxSteps) },
  ];
}

function holdReason(left: Outcome, right: Outcome): HeldOut["reason"] | null {
  const outcomes = [left, right];
  if (outcomes.some((outcome) => outcome === "env_error")) return "env";
  if (outcomes.some((outcome) => outcome === "cancelled")) return "cancelled";
  if (outcomes.some((outcome) => outcome === "budget_exhausted")) return "budget";
  if (outcomes.some((outcome) => outcome === "pending" || outcome === "running")) return "unfinished";
  if (outcomes.some((outcome) => outcome === "missing")) return "missing";
  return null;
}

function modelSide(outcome: Outcome): "success" | "fail" | null {
  if (outcome === "correct") return "success";
  if (outcome === "wrong") return "fail";
  return null;
}

export function compareExperiments(left: Experiment, right: Experiment): Comparison {
  const leftRows = configRows(left);
  const rightRows = configRows(right);
  const configChanges = leftRows
    .filter((row, index) => row.value !== rightRows[index]?.value)
    .map((row) => ({
      key: row.key,
      left: row.value,
      right: rightRows.find((item) => item.key === row.key)?.value ?? "",
    }));
  const notes = ["两边都是演示数据，不能写成真实实验结论。"];
  const sameBenchmark = left.benchmarkId === right.benchmarkId;
  if (!sameBenchmark) {
    notes.push("两次运行的评测集不同。不合并成一个分数，也不做逐题配对。");
  }
  const changedKeys = configChanges.map((row) => row.key);
  const confounders = changedKeys.filter((key) => !["模型", "版本"].includes(key) || key === "版本");
  const onlyModel =
    changedKeys.length > 0 && changedKeys.every((key) => key === "模型" || key === "版本") && changedKeys.includes("模型") && !changedKeys.includes("版本") && changedKeys.length === 1;
  if (sameBenchmark && left.modelId === right.modelId && changedKeys.length > 0) {
    notes.push(`模型相同。配置差异是：${changedKeys.join("、")}。不能把成绩变化归因于模型。`);
  } else if (sameBenchmark && left.modelId !== right.modelId && confounders.some((key) => key !== "模型")) {
    const extra = changedKeys.filter((key) => key !== "模型");
    notes.push(`除模型外还有差异：${extra.join("、")}。不能把变化全部归因于模型。`);
  } else if (sameBenchmark && onlyModel) {
    notes.push("配置里只有模型不同。这仍是演示脚本，不是一次真实对照实验。");
  }
  if (left.agentId !== right.agentId || left.runtimeId !== right.runtimeId) {
    notes.push("Agent 或执行环境不同。这是整套系统的差异，不能记成执行器本身的差别。");
  }
  const leftSummary = summarize(left);
  const rightSummary = summarize(right);
  const tokensComparable =
    leftSummary.tokens.costKind !== "unknown" &&
    rightSummary.tokens.costKind !== "unknown" &&
    leftSummary.tokens.inputTokens != null &&
    rightSummary.tokens.inputTokens != null;
  if (!tokensComparable) notes.push("用量覆盖不一致或缺失，token 和费用不能比较。");

  const buckets = {
    failSuccess: [] as PairItem[],
    successFail: [] as PairItem[],
    bothSuccess: [] as PairItem[],
    bothFail: [] as PairItem[],
  };
  const held: HeldOut[] = [];
  if (sameBenchmark) {
    const keyOf = (sample: Sample) => `${sample.taskId}#${sample.repeat}`;
    const leftMap = new Map(left.samples.map((sample) => [keyOf(sample), sample]));
    const rightMap = new Map(right.samples.map((sample) => [keyOf(sample), sample]));
    const keys = [...new Set([...leftMap.keys(), ...rightMap.keys()])].sort();
    for (const key of keys) {
      const a = leftMap.get(key);
      const b = rightMap.get(key);
      const [taskId, repeatText] = key.split("#");
      const repeat = Number(repeatText);
      if (!a || !b) {
        held.push({
          reason: "unmatched",
          taskId,
          repeat,
          detail: "这道题只出现在一侧，不参与配对。",
          leftOutcome: a?.outcome ?? null,
          rightOutcome: b?.outcome ?? null,
        });
        continue;
      }
      if (a.taskFingerprint && b.taskFingerprint && a.taskFingerprint !== b.taskFingerprint) {
        held.push({
          reason: "identity",
          taskId,
          repeat,
          detail: "同名题目的指纹不同，不配对。",
          leftOutcome: a.outcome,
          rightOutcome: b.outcome,
        });
        continue;
      }
      const reason = holdReason(a.outcome, b.outcome);
      if (reason) {
        held.push({
          reason,
          taskId,
          repeat,
          detail: holdDetail(reason),
          leftOutcome: a.outcome,
          rightOutcome: b.outcome,
        });
        continue;
      }
      const leftSide = modelSide(a.outcome);
      const rightSide = modelSide(b.outcome);
      const item = { taskId, repeat, left: a, right: b };
      if (leftSide === "fail" && rightSide === "success") buckets.failSuccess.push(item);
      else if (leftSide === "success" && rightSide === "fail") buckets.successFail.push(item);
      else if (leftSide === "success" && rightSide === "success") buckets.bothSuccess.push(item);
      else if (leftSide === "fail" && rightSide === "fail") buckets.bothFail.push(item);
    }
  }

  const tags = new Set<string>();
  for (const taskId of new Set([...left.taskIds, ...right.taskIds])) {
    for (const tag of taskById(taskId)?.tags ?? []) tags.add(tag);
  }
  const capability = [...tags].sort().map((tag) => {
    const leftSide = sideTag(left, tag);
    const rightSide = sideTag(right, tag);
    return {
      tag,
      leftCorrect: leftSide.correct,
      leftN: leftSide.n,
      rightCorrect: rightSide.correct,
      rightN: rightSide.n,
    };
  });

  return {
    sameBenchmark,
    notes,
    configChanges,
    buckets,
    held,
    leftRate: sameBenchmark ? leftSummary.successRate : null,
    rightRate: sameBenchmark ? rightSummary.successRate : null,
    percentagePoints:
      sameBenchmark && leftSummary.successRate != null && rightSummary.successRate != null
        ? (rightSummary.successRate - leftSummary.successRate) * 100
        : null,
    tokensComparable,
    capability,
  };
}

function holdDetail(reason: HeldOut["reason"]): string {
  switch (reason) {
    case "env":
      return "含环境异常，不归入模型答错。";
    case "cancelled":
      return "含取消样本，不归入模型答错。";
    case "budget":
      return "含预算耗尽。预算变化不记成模型由错变对。";
    case "unfinished":
      return "还有未结束的样本，暂不配对。";
    case "missing":
      return "记录缺失，不配对。";
    default:
      return "不配对。";
  }
}

function sideTag(experiment: Experiment, tag: string): { correct: number; n: number } {
  const samples = experiment.samples.filter((sample) => taskById(sample.taskId)?.tags.includes(tag));
  return {
    n: samples.length,
    correct: samples.filter((sample) => sample.outcome === "correct").length,
  };
}
