import { describe, expect, it } from "vitest";
import { findSeed } from "../data/seed";
import { compareExperiments, summarize } from "./metrics";
import { parseDemoInstruction } from "./nl";
import { cancelExperiment, createDraft, startExperiment, tick } from "./simulate";
import { splitHighlight, visibleText } from "./text";

describe("recorded text", () => {
  it("strips terminal color and keeps markup as text", () => {
    expect(visibleText("\u001b[31mCONFLICT\u001b[0m <img src=x onerror=alert(1)>")).toBe(
      "CONFLICT <img src=x onerror=alert(1)>",
    );
    expect(splitHighlight("git merge --no-edit recovered", "git merge")[0]).toEqual({
      text: "git merge",
      hit: true,
    });
  });
});

describe("demo metrics", () => {
  const low = findSeed("exp-ds-basic-30k");
  const high = findSeed("exp-ds-basic-60k");
  const qwen = findSeed("exp-qwen-basic-30k");
  const gsm = findSeed("exp-ds-gsm8k");

  it("keeps the denominator and does not treat environment or budget as model failure", () => {
    const summary = summarize(low);
    expect(summary.planned).toBe(5);
    expect(summary.correct).toBe(2);
    expect(summary.successRate).toBeCloseTo(0.4);
    expect(summary.modelFailures).toBe(1);
    expect(summary.envErrors).toBe(1);
    expect(summary.budget).toBe(1);
    expect(summary.tokens.inputTokens).toBe(45_300);
    expect(summary.tokens.cachedTokens).toBe(3_800);
    expect(summary.tokens.outputTokens).toBe(4_100);
    expect(summary.tokens.costCny).toBeCloseTo(0.0617);
    expect(summary.tokens.cacheNote).toContain("不再把缓存加到输入");
    expect(summary.time.taskSeconds).toBe(365);
    expect(summary.steps).toBeGreaterThan(summary.toolCalls);
  });

  it("does not fill missing usage or time with zero", () => {
    const summary = summarize(high);
    expect(summary.tokens.inputTokens).toBeNull();
    expect(summary.tokens.costKind).toBe("unknown");
    expect(summary.time.taskSeconds).toBeNull();
    const sqlite = low.samples.find((sample) => sample.taskId === "sqlite-db-truncate");
    expect(sqlite?.usage.status).toBe("not_applicable");
    expect(sqlite?.usage.inputTokens).toBeNull();
    expect(sqlite?.score).toBeNull();
  });

  it("pairs model results and holds out budget, environment, and other benchmarks", () => {
    const budget = compareExperiments(low, high);
    expect(budget.percentagePoints).toBeCloseTo(20);
    expect(budget.buckets.failSuccess.map((item) => item.taskId)).toEqual(["regex-log"]);
    expect(budget.buckets.successFail.map((item) => item.taskId)).toEqual(["openssl-selfsigned-cert"]);
    expect(budget.held.find((item) => item.taskId === "fix-git")?.reason).toBe("budget");
    expect(budget.held.find((item) => item.taskId === "sqlite-db-truncate")?.reason).toBe("env");
    expect(budget.tokensComparable).toBe(false);
    expect(budget.notes.some((note) => note.includes("不能把成绩变化归因于模型"))).toBe(true);

    const modelsOnly = compareExperiments(low, qwen);
    expect(modelsOnly.notes.some((note) => note.includes("只有模型不同"))).toBe(true);
    expect(modelsOnly.buckets.bothSuccess.map((item) => item.taskId)).toContain("log-summary-date-ranges");
    expect(modelsOnly.held.find((item) => item.taskId === "sqlite-db-truncate")?.reason).toBe("env");

    const mixed = compareExperiments(low, gsm);
    expect(mixed.sameBenchmark).toBe(false);
    expect(mixed.percentagePoints).toBeNull();
    expect(mixed.buckets.bothSuccess).toHaveLength(0);
  });
});

describe("simulated run", () => {
  it("stops the clock and progress after cancel", () => {
    const draft = createDraft(
      {
        name: "取消试验",
        benchmarkId: "terminal-bench",
        taskIds: ["log-summary-date-ranges", "regex-log"],
        modelId: "deepseek-flash",
        agentId: "basic",
        runtimeId: "native",
        repeats: 1,
        concurrency: 1,
        maxTokens: 30_000,
        maxSteps: null,
      },
      1_000,
      "exp-cancel",
    );
    const started = startExperiment(draft, 1_000);
    const running = tick(started, 1_000);
    const later = tick(running, 4_000);
    const stopped = cancelExperiment(later, 4_500);
    const after = tick(stopped, 20_000);
    expect(after.status).toBe("cancelled");
    expect(after.wallClockS).toBe(stopped.wallClockS);
    expect(after.samples.map((sample) => sample.outcome)).toEqual(
      stopped.samples.map((sample) => sample.outcome),
    );
    expect(after.samples.some((sample) => sample.outcome === "running" || sample.outcome === "pending")).toBe(
      false,
    );
    expect(after.samples[0]?.outcome).toBe("correct");
    expect(after.samples[1]?.outcome).toBe("cancelled");
    expect(summarize(after).progressed).toBe(1);
    expect(after.samples.filter((sample) => sample.outcome === "wrong")).toHaveLength(0);
  });
});

describe("demo parser", () => {
  it("fills the supported example and rejects everything else", () => {
    const parsed = parseDemoInstruction("用模型 A 跑 5 道终端题，每题 3 次，并发 2。");
    expect(parsed.ok).toBe(true);
    if (parsed.ok) {
      expect(parsed.draft.modelId).toBe("deepseek-flash");
      expect(parsed.draft.benchmarkId).toBe("terminal-bench");
      expect(parsed.draft.taskIds).toHaveLength(5);
      expect(parsed.draft.repeats).toBe(3);
      expect(parsed.draft.concurrency).toBe(2);
      expect(parsed.note).toContain("演示解析");
    }
    expect(parseDemoInstruction("你好").ok).toBe(false);
    const swe = parseDemoInstruction("用模型 A 跑 SWE-bench");
    expect(swe.ok).toBe(false);
    if (!swe.ok) expect(swe.message).toContain("没有接入");
  });
});
