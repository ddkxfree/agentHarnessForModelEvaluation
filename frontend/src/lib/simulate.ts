import { agentById, benchmarkById, modelById, taskById } from "../data/catalog";
import { evidenceFor, stepsFor } from "../data/traces";
import { END_REASON } from "./format";
import type { DraftInput, Experiment, ExperimentEvent, Outcome, Sample, Usage } from "../types";

const NA: Usage = {
  status: "not_applicable",
  inputTokens: null,
  outputTokens: null,
  cachedTokens: null,
  cacheIncludedInInput: null,
};

const UNKNOWN: Usage = {
  status: "unknown",
  inputTokens: null,
  outputTokens: null,
  cachedTokens: null,
  cacheIncludedInInput: null,
};

export function reportedUsage(input: number, output: number, cached: number): Usage {
  return {
    status: "reported",
    inputTokens: input,
    outputTokens: output,
    cachedTokens: cached,
    cacheIncludedInInput: true,
  };
}

export function unknownUsage(): Usage {
  return { ...UNKNOWN };
}

export function notApplicableUsage(): Usage {
  return { ...NA };
}

export function scriptOutcome(
  taskId: string,
  repeat: number,
  maxTokens: number | null,
  runtimeId: string,
): Outcome {
  if (maxTokens === 0) return "budget_exhausted";
  if (runtimeId === "harbor" && taskId === "sqlite-db-truncate") return "env_error";
  if (taskId === "fix-git" && (maxTokens == null || maxTokens <= 30_000)) return "budget_exhausted";
  if (taskId === "sqlite-db-truncate") return "env_error";
  if (repeat % 3 === 2) return "wrong";
  if (taskId === "regex-log" && repeat % 2 === 1) return "wrong";
  return "correct";
}

function usageFor(outcome: Outcome, taskId: string): Usage {
  if (outcome === "env_error" || outcome === "cancelled" || outcome === "pending" || outcome === "missing") {
    return outcome === "cancelled" ? { ...UNKNOWN } : { ...NA };
  }
  const scale = taskId.length;
  return reportedUsage(1_000 + scale * 80, 200 + scale * 10, 100);
}

function scoreFor(outcome: Outcome): number | null {
  if (outcome === "correct") return 1;
  if (outcome === "wrong" || outcome === "budget_exhausted") return 0;
  return null;
}

export function buildPending(experiment: Pick<
  Experiment,
  "taskIds" | "repeats" | "maxTokens" | "runtimeId" | "benchmarkId"
>): Sample[] {
  const samples: Sample[] = [];
  for (const taskId of experiment.taskIds) {
    const task = taskById(taskId);
    for (let repeat = 0; repeat < experiment.repeats; repeat += 1) {
      const outcome = scriptOutcome(taskId, repeat, experiment.maxTokens, experiment.runtimeId);
      samples.push({
        id: `${taskId}#r${repeat}`,
        taskId,
        repeat,
        outcome: "pending",
        endReason: END_REASON.pending,
        score: null,
        wallTimeS: null,
        usage: { ...UNKNOWN },
        steps: [],
        evidence: "",
        taskFingerprint: task?.fingerprint ?? "missing",
        demoScript: outcome,
        runningSince: null,
      });
    }
  }
  return samples;
}

function event(nowMs: number, text: string, suffix: string): ExperimentEvent {
  return { id: `${suffix}-${nowMs}`, at: new Date(nowMs).toISOString(), text };
}

export function createDraft(input: DraftInput, nowMs: number, id: string): Experiment {
  const benchmark = benchmarkById(input.benchmarkId);
  const model = modelById(input.modelId);
  const agent = agentById(input.agentId);
  return {
    id,
    name: input.name.trim() || `${model?.label ?? input.modelId} · ${agent?.label ?? input.agentId}`,
    dataKind: "demo",
    benchmarkId: input.benchmarkId,
    benchmarkVersion: benchmark?.version ?? "未记录",
    taskIds: [...input.taskIds],
    modelId: input.modelId,
    agentId: input.agentId,
    runtimeId: input.runtimeId,
    repeats: input.repeats,
    concurrency: input.concurrency,
    maxTokens: input.maxTokens,
    maxSteps: input.maxSteps,
    status: "draft",
    createdAt: new Date(nowMs).toISOString(),
    startedAt: null,
    finishedAt: null,
    wallClockS: null,
    updatedAtMs: null,
    samples: [],
    events: [event(nowMs, "已创建草稿。还没有模拟运行。", "draft")],
  };
}

export function updateDraft(experiment: Experiment, input: DraftInput, nowMs: number): Experiment {
  if (experiment.status !== "draft") return experiment;
  const next = createDraft(input, nowMs, experiment.id);
  return {
    ...next,
    createdAt: experiment.createdAt,
    events: [
      ...experiment.events,
      event(nowMs, "已修改配置。运行开始前可以继续改。", "edit"),
    ],
  };
}

export function startExperiment(experiment: Experiment, nowMs: number): Experiment {
  if (experiment.status !== "draft") return experiment;
  const samples = buildPending(experiment);
  return {
    ...experiment,
    status: "running",
    startedAt: new Date(nowMs).toISOString(),
    finishedAt: null,
    wallClockS: 0,
    updatedAtMs: null,
    samples,
    events: [
      ...experiment.events,
      event(nowMs, "开始模拟运行。没有调用模型，也没有启动 Docker。", "start"),
    ],
  };
}

function clockOf(experiment: Experiment, nowMs: number): number {
  const base = experiment.wallClockS ?? 0;
  if (experiment.updatedAtMs == null) return base;
  return base + Math.max(0, nowMs - experiment.updatedAtMs) / 1000;
}

function completeSample(sample: Sample): Sample {
  const outcome = sample.demoScript ?? "correct";
  return {
    ...sample,
    outcome,
    endReason: END_REASON[outcome],
    score: scoreFor(outcome),
    wallTimeS: outcome === "env_error" ? 6 : outcome === "budget_exhausted" ? 120 : 30,
    usage: usageFor(outcome, sample.taskId),
    steps: stepsFor(sample.taskId, outcome).map((item) => ({
      ...item,
      id: `${sample.id}-${item.id}`,
    })),
    evidence: evidenceFor(sample.taskId, outcome),
    runningSince: null,
  };
}

export function cancelExperiment(experiment: Experiment, nowMs: number): Experiment {
  if (experiment.status !== "running") return experiment;
  return {
    ...experiment,
    status: "cancelled",
    finishedAt: new Date(nowMs).toISOString(),
    wallClockS: Math.round(clockOf(experiment, nowMs) * 10) / 10,
    updatedAtMs: null,
    samples: experiment.samples.map((sample) => {
      if (
        sample.outcome === "correct" ||
        sample.outcome === "wrong" ||
        sample.outcome === "budget_exhausted" ||
        sample.outcome === "env_error" ||
        sample.outcome === "missing"
      ) {
        return sample;
      }
      if (sample.outcome === "pending") {
        return {
          ...sample,
          outcome: "cancelled",
          endReason: END_REASON.cancelled,
          score: null,
          wallTimeS: null,
          usage: { ...NA },
          runningSince: null,
          evidence: "取消时尚未开始，没有模型调用。用量不适用，不是 0。",
        };
      }
      return {
        ...sample,
        outcome: "cancelled",
        endReason: END_REASON.cancelled,
        score: null,
        wallTimeS: sample.wallTimeS,
        usage: { ...UNKNOWN },
        runningSince: null,
        evidence: "取消时正在进行。已记录的步骤保留；还没记下来的用量保持未记录，不填 0。",
      };
    }),
    events: [
      ...experiment.events,
      event(nowMs, "已取消。计时和进度停止，未完成的样本不记为模型失败。", "cancel"),
    ],
  };
}

export function tick(experiment: Experiment, nowMs: number): Experiment {
  if (experiment.status !== "running") return experiment;
  const wall = Math.round(clockOf(experiment, nowMs) * 10) / 10;
  let samples = experiment.samples.map((sample) => {
    if (sample.outcome !== "running") return sample;
    if (sample.runningSince == null) return { ...sample, runningSince: nowMs };
    if (nowMs - sample.runningSince < 2000) return sample;
    return completeSample(sample);
  });
  let slots = experiment.concurrency - samples.filter((sample) => sample.outcome === "running").length;
  samples = samples.map((sample) => {
    if (slots > 0 && sample.outcome === "pending") {
      slots -= 1;
      return { ...sample, outcome: "running", endReason: END_REASON.running, runningSince: nowMs };
    }
    return sample;
  });
  const events = [...experiment.events];
  samples.forEach((sample, index) => {
    const previous = experiment.samples[index];
    if (previous && previous.outcome === "running" && sample.outcome !== "running") {
      events.push(
        event(
          nowMs,
          `${sample.taskId} 第 ${sample.repeat + 1} 次结束：${sample.endReason}。`,
          sample.id,
        ),
      );
    }
  });
  const done = samples.every((sample) => sample.outcome !== "pending" && sample.outcome !== "running");
  return {
    ...experiment,
    status: done ? "completed" : "running",
    finishedAt: done ? new Date(nowMs).toISOString() : null,
    wallClockS: wall,
    updatedAtMs: nowMs,
    samples,
    events,
  };
}
