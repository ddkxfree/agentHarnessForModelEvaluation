import { agentById, benchmarkById, modelById, runtimeById } from "../data/catalog";
import type { DraftInput } from "../types";

export function validateDraft(input: DraftInput): string | null {
  const benchmark = benchmarkById(input.benchmarkId);
  if (!benchmark) return "没有这个评测集。";
  if (!benchmark.canCreate) return `${benchmark.name} 还没有接入，不能创建实验。`;
  if (input.taskIds.length === 0) return "至少选择一道题。";
  const unknown = input.taskIds.filter((id) => !benchmark.tasks.some((task) => task.id === id));
  if (unknown.length > 0) return "有题目不属于所选评测集。";
  if (!modelById(input.modelId)) return "没有这个演示模型。";
  if (!agentById(input.agentId)) return "没有这个 Agent。";
  if (!runtimeById(input.runtimeId)) return "没有这个执行环境。";
  if (!Number.isInteger(input.repeats) || input.repeats < 1 || input.repeats > 5) {
    return "重复次数只接受 1 到 5。";
  }
  if (!Number.isInteger(input.concurrency) || input.concurrency < 1 || input.concurrency > 4) {
    return "并发只接受 1 到 4。";
  }
  if (input.maxTokens != null && (!Number.isInteger(input.maxTokens) || input.maxTokens < 0)) {
    return "token 上限要是空着（未设置）或非负整数。0 会按预算耗尽结束，不是缺失。";
  }
  if (input.maxSteps != null && (!Number.isInteger(input.maxSteps) || input.maxSteps < 1)) {
    return "步数上限要是空着（未设置）或不小于 1 的整数。";
  }
  return null;
}
