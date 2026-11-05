import { agentById, modelById, runtimeById } from "../data/catalog";
import type { Outcome, Usage } from "../types";

export const OUTCOME_LABEL: Record<Outcome, string> = {
  correct: "通过",
  wrong: "模型失败",
  budget_exhausted: "预算耗尽",
  env_error: "环境异常",
  cancelled: "取消",
  running: "进行中",
  pending: "未开始",
  missing: "缺失",
};

export const STATUS_LABEL = {
  draft: "草稿",
  running: "运行中",
  completed: "已完成",
  cancelled: "已取消",
} as const;

export const END_REASON: Record<Outcome, string> = {
  correct: "测试通过",
  wrong: "测试未通过",
  budget_exhausted: "达到 token 预算",
  env_error: "环境准备失败",
  cancelled: "用户取消",
  running: "进行中",
  pending: "未开始",
  missing: "记录缺失",
};

export const STEP_DEFINITION =
  "一步是时间线上的一条已记录消息、工具调用或工具返回。工具调用数只计算工具调用，不把两者当成同一个数。";

export function formatInt(value: number | null, missing = "未记录"): string {
  if (value == null) return missing;
  return new Intl.NumberFormat("zh-CN").format(value);
}

export function formatSeconds(value: number | null, missing = "未记录"): string {
  if (value == null) return missing;
  return `${value.toFixed(1)} 秒`;
}

export function formatPercent(rate: number | null): string {
  if (rate == null) return "未记录";
  return `${(rate * 100).toFixed(1)}%`;
}

export function formatPoints(points: number | null): string {
  if (points == null) return "未记录";
  const sign = points > 0 ? "+" : "";
  return `${sign}${points.toFixed(1)} 个百分点`;
}

export function formatWhen(iso: string | null): string {
  if (!iso) return "未记录";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "未记录";
  return `${new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).format(date)} 北京时间`;
}

export function usageValue(usage: Usage, field: "inputTokens" | "outputTokens" | "cachedTokens"): string {
  if (usage.status === "not_applicable") return "不适用";
  return formatInt(usage[field]);
}

export function cacheNote(usage: Usage): string {
  if (usage.status === "not_applicable") return "没有模型调用，缓存不适用。";
  if (usage.status !== "reported" || usage.cacheIncludedInInput == null) {
    return "未记录缓存是否已计入输入。页面不把输入和缓存相加。";
  }
  return usage.cacheIncludedInInput
    ? "缓存已计入输入。页面不把缓存再加到输入上。"
    : "记录说明缓存未计入输入。页面仍分开显示，不自动相加。";
}

export function modelLabel(id: string): string {
  const model = modelById(id);
  if (!model) return id;
  return model.alias ? `${model.label}（${model.alias}）` : model.label;
}

export function agentLabel(id: string): string {
  return agentById(id)?.label ?? id;
}

export function runtimeLabel(id: string): string {
  return runtimeById(id)?.label ?? id;
}

export function limitLabel(value: number | null, unit: string): string {
  if (value == null) return "未设置";
  return `${formatInt(value)} ${unit}`;
}
