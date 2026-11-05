import { benchmarkById, taskById } from "./catalog";
import { evidenceFor, stepsFor } from "./traces";
import { END_REASON } from "../lib/format";
import { notApplicableUsage, reportedUsage, unknownUsage } from "../lib/simulate";
import type { Experiment, Outcome, Sample, Usage } from "../types";

function done(input: {
  taskId: string;
  outcome: Outcome;
  wallTimeS: number | null;
  usage: Usage;
  score: number | null;
}): Sample {
  const task = taskById(input.taskId);
  return {
    id: `${input.taskId}#r0`,
    taskId: input.taskId,
    repeat: 0,
    outcome: input.outcome,
    endReason: END_REASON[input.outcome],
    score: input.score,
    wallTimeS: input.wallTimeS,
    usage: input.usage,
    steps: stepsFor(input.taskId, input.outcome).map((item) => ({
      ...item,
      id: `${input.taskId}#r0-${item.id}`,
    })),
    evidence: evidenceFor(input.taskId, input.outcome),
    taskFingerprint: task?.fingerprint ?? "missing",
    runningSince: null,
  };
}

function base(
  partial: Pick<
    Experiment,
    "id" | "name" | "benchmarkId" | "modelId" | "agentId" | "runtimeId" | "maxTokens" | "concurrency" | "status" | "wallClockS" | "samples"
  > & { repeats?: number; createdAt: string; startedAt: string | null; finishedAt: string | null },
): Experiment {
  const benchmark = benchmarkById(partial.benchmarkId);
  const { repeats, ...rest } = partial;
  return {
    ...rest,
    dataKind: "demo",
    benchmarkVersion: benchmark?.version ?? "未记录",
    taskIds: partial.samples.reduce<string[]>((ids, sample) => {
      if (!ids.includes(sample.taskId)) ids.push(sample.taskId);
      return ids;
    }, []),
    repeats: repeats ?? 1,
    maxSteps: null,
    updatedAtMs: null,
    events: [
      {
        id: `${partial.id}-seed`,
        at: partial.createdAt,
        text: "演示目录中的记录。不是一次真实运行。",
      },
    ],
  };
}

const terminalTasks = [
  "fix-git",
  "log-summary-date-ranges",
  "regex-log",
  "openssl-selfsigned-cert",
  "sqlite-db-truncate",
];

function runningSamples(): Sample[] {
  const scripts: Outcome[] = [
    "correct",
    "wrong",
    "correct",
    "env_error",
    "budget_exhausted",
    "correct",
    "wrong",
    "correct",
    "correct",
    "wrong",
  ];
  const samples: Sample[] = [];
  let index = 0;
  for (const taskId of terminalTasks) {
    for (let repeat = 0; repeat < 2; repeat += 1) {
      const task = taskById(taskId);
      const script = scripts[index] ?? "correct";
      const outcome: Outcome = index === 0 ? "correct" : index === 1 ? "running" : "pending";
      samples.push({
        id: `${taskId}#r${repeat}`,
        taskId,
        repeat,
        outcome,
        endReason: END_REASON[outcome],
        score: outcome === "correct" ? 1 : null,
        wallTimeS: outcome === "correct" ? 36 : null,
        usage: outcome === "correct" ? reportedUsage(3200, 480, 200) : unknownUsage(),
        steps:
          outcome === "correct"
            ? stepsFor(taskId, "correct").map((item) => ({ ...item, id: `${taskId}#r${repeat}-${item.id}` }))
            : [],
        evidence: outcome === "correct" ? evidenceFor(taskId, "correct") : "",
        taskFingerprint: task?.fingerprint ?? "missing",
        demoScript: script,
        runningSince: null,
      });
      index += 1;
    }
  }
  return samples;
}

export function seedExperiments(): Experiment[] {
  const a = base({
    id: "exp-ds-basic-30k",
    name: "DeepSeek · BasicAgent · 30k",
    benchmarkId: "terminal-bench",
    modelId: "deepseek-flash",
    agentId: "basic",
    runtimeId: "native",
    maxTokens: 30_000,
    concurrency: 2,
    status: "completed",
    wallClockS: 420,
    createdAt: "2026-11-04T02:10:00.000Z",
    startedAt: "2026-11-04T02:12:00.000Z",
    finishedAt: "2026-11-04T02:19:00.000Z",
    samples: [
      done({
        taskId: "fix-git",
        outcome: "budget_exhausted",
        wallTimeS: 186,
        usage: reportedUsage(28_000, 1_900, 2_000),
        score: 0,
      }),
      done({
        taskId: "log-summary-date-ranges",
        outcome: "correct",
        wallTimeS: 42,
        usage: reportedUsage(4_200, 600, 800),
        score: 1,
      }),
      done({
        taskId: "regex-log",
        outcome: "wrong",
        wallTimeS: 55,
        usage: reportedUsage(5_100, 700, 0),
        score: 0,
      }),
      done({
        taskId: "openssl-selfsigned-cert",
        outcome: "correct",
        wallTimeS: 70,
        usage: reportedUsage(8_000, 900, 1_000),
        score: 1,
      }),
      done({
        taskId: "sqlite-db-truncate",
        outcome: "env_error",
        wallTimeS: 12,
        usage: notApplicableUsage(),
        score: null,
      }),
    ],
  });

  const b = base({
    id: "exp-ds-basic-60k",
    name: "DeepSeek · BasicAgent · 60k",
    benchmarkId: "terminal-bench",
    modelId: "deepseek-flash",
    agentId: "basic",
    runtimeId: "native",
    maxTokens: 60_000,
    concurrency: 2,
    status: "completed",
    wallClockS: 510,
    createdAt: "2026-11-04T03:00:00.000Z",
    startedAt: "2026-11-04T03:02:00.000Z",
    finishedAt: "2026-11-04T03:10:30.000Z",
    samples: [
      done({
        taskId: "fix-git",
        outcome: "correct",
        wallTimeS: 95,
        usage: reportedUsage(15_000, 500, 400),
        score: 1,
      }),
      done({
        taskId: "log-summary-date-ranges",
        outcome: "correct",
        wallTimeS: 40,
        usage: reportedUsage(4_000, 500, 200),
        score: 1,
      }),
      done({
        taskId: "regex-log",
        outcome: "correct",
        wallTimeS: 48,
        usage: reportedUsage(6_000, 800, 100),
        score: 1,
      }),
      done({
        taskId: "openssl-selfsigned-cert",
        outcome: "wrong",
        wallTimeS: null,
        usage: unknownUsage(),
        score: 0,
      }),
      done({
        taskId: "sqlite-db-truncate",
        outcome: "env_error",
        wallTimeS: 8,
        usage: notApplicableUsage(),
        score: null,
      }),
    ],
  });

  const c = base({
    id: "exp-qwen-basic-30k",
    name: "Qwen · BasicAgent · 30k",
    benchmarkId: "terminal-bench",
    modelId: "qwen-plus",
    agentId: "basic",
    runtimeId: "native",
    maxTokens: 30_000,
    concurrency: 2,
    status: "completed",
    wallClockS: 390,
    createdAt: "2026-11-04T04:00:00.000Z",
    startedAt: "2026-11-04T04:01:00.000Z",
    finishedAt: "2026-11-04T04:07:30.000Z",
    samples: [
      done({ taskId: "fix-git", outcome: "wrong", wallTimeS: 80, usage: reportedUsage(9_000, 700, 300), score: 0 }),
      done({
        taskId: "log-summary-date-ranges",
        outcome: "correct",
        wallTimeS: 38,
        usage: reportedUsage(3_800, 420, 150),
        score: 1,
      }),
      done({ taskId: "regex-log", outcome: "correct", wallTimeS: 44, usage: reportedUsage(4_600, 510, 80), score: 1 }),
      done({
        taskId: "openssl-selfsigned-cert",
        outcome: "correct",
        wallTimeS: 66,
        usage: reportedUsage(7_200, 640, 220),
        score: 1,
      }),
      done({
        taskId: "sqlite-db-truncate",
        outcome: "env_error",
        wallTimeS: 9,
        usage: notApplicableUsage(),
        score: null,
      }),
    ],
  });

  const harbor = base({
    id: "exp-ds-terminus",
    name: "DeepSeek · Terminus-2 · Harbor",
    benchmarkId: "terminal-bench",
    modelId: "deepseek-flash",
    agentId: "terminus-2",
    runtimeId: "harbor",
    maxTokens: 30_000,
    concurrency: 1,
    status: "completed",
    wallClockS: 640,
    createdAt: "2026-11-04T05:00:00.000Z",
    startedAt: "2026-11-04T05:03:00.000Z",
    finishedAt: "2026-11-04T05:13:40.000Z",
    samples: [
      done({ taskId: "fix-git", outcome: "wrong", wallTimeS: 140, usage: reportedUsage(18_000, 1_200, 600), score: 0 }),
      done({
        taskId: "log-summary-date-ranges",
        outcome: "correct",
        wallTimeS: 50,
        usage: reportedUsage(5_000, 540, 240),
        score: 1,
      }),
      done({ taskId: "regex-log", outcome: "wrong", wallTimeS: 61, usage: reportedUsage(5_400, 680, 0), score: 0 }),
      done({
        taskId: "openssl-selfsigned-cert",
        outcome: "correct",
        wallTimeS: 77,
        usage: reportedUsage(8_400, 710, 300),
        score: 1,
      }),
      done({
        taskId: "sqlite-db-truncate",
        outcome: "env_error",
        wallTimeS: 11,
        usage: notApplicableUsage(),
        score: null,
      }),
    ],
  });

  const gsm = base({
    id: "exp-ds-gsm8k",
    name: "DeepSeek · GSM8K 演示子集",
    benchmarkId: "gsm8k-demo",
    modelId: "deepseek-flash",
    agentId: "basic",
    runtimeId: "native",
    maxTokens: 4_000,
    concurrency: 1,
    status: "completed",
    wallClockS: 28,
    createdAt: "2026-11-04T06:00:00.000Z",
    startedAt: "2026-11-04T06:01:00.000Z",
    finishedAt: "2026-11-04T06:01:28.000Z",
    samples: ["gsm-pens", "gsm-shelves", "gsm-tickets", "gsm-ribbon"].map((taskId, index) =>
      done({
        taskId,
        outcome: index === 3 ? "wrong" : "correct",
        wallTimeS: 4 + index,
        usage: reportedUsage(900 + index * 40, 120, 0),
        score: index === 3 ? 0 : 1,
      }),
    ),
  });

  const running = base({
    id: "exp-running",
    name: "演示运行 · Terminal-Bench",
    benchmarkId: "terminal-bench",
    modelId: "deepseek-flash",
    agentId: "basic",
    runtimeId: "native",
    maxTokens: 30_000,
    concurrency: 1,
    repeats: 2,
    status: "running",
    wallClockS: 35,
    createdAt: "2026-11-05T01:00:00.000Z",
    startedAt: "2026-11-05T01:02:00.000Z",
    finishedAt: null,
    samples: runningSamples(),
  });
  running.taskIds = [...terminalTasks];

  return [running, a, b, c, harbor, gsm];
}

export function findSeed(id: string): Experiment {
  const experiment = seedExperiments().find((item) => item.id === id);
  if (!experiment) throw new Error(`missing seed ${id}`);
  return experiment;
}
