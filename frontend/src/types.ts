export type ExperimentStatus = "draft" | "running" | "completed" | "cancelled";

export type Outcome =
  | "correct"
  | "wrong"
  | "budget_exhausted"
  | "env_error"
  | "cancelled"
  | "running"
  | "pending"
  | "missing";

export type UsageStatus = "reported" | "unknown" | "not_applicable";

export interface Usage {
  status: UsageStatus;
  inputTokens: number | null;
  outputTokens: number | null;
  cachedTokens: number | null;
  /** True when cached tokens are already included in inputTokens. */
  cacheIncludedInInput: boolean | null;
}

export interface Step {
  id: string;
  kind: "message" | "tool_call" | "tool_result";
  actor: "user" | "model" | "tool" | "environment";
  tool?: string;
  command?: string;
  text: string;
  error?: boolean;
  failureCause?: boolean;
  durationS: number | null;
  inputTokens: number | null;
  outputTokens: number | null;
}

export interface Sample {
  id: string;
  taskId: string;
  repeat: number;
  outcome: Outcome;
  endReason: string;
  score: number | null;
  wallTimeS: number | null;
  usage: Usage;
  steps: Step[];
  evidence: string;
  taskFingerprint: string;
  /** Demo script used when a simulated sample finishes. Not a recorded result. */
  demoScript?: Outcome;
  runningSince: number | null;
}

export interface ExperimentEvent {
  id: string;
  at: string;
  text: string;
}

export interface Experiment {
  id: string;
  name: string;
  dataKind: "demo";
  benchmarkId: string;
  benchmarkVersion: string;
  taskIds: string[];
  modelId: string;
  agentId: string;
  runtimeId: string;
  repeats: number;
  concurrency: number;
  maxTokens: number | null;
  maxSteps: number | null;
  status: ExperimentStatus;
  createdAt: string;
  startedAt: string | null;
  finishedAt: string | null;
  wallClockS: number | null;
  /** In-memory anchor for the simulated clock. Stripped before persistence. */
  updatedAtMs: number | null;
  samples: Sample[];
  events: ExperimentEvent[];
}

export interface DraftInput {
  name: string;
  benchmarkId: string;
  taskIds: string[];
  modelId: string;
  agentId: string;
  runtimeId: string;
  repeats: number;
  concurrency: number;
  maxTokens: number | null;
  maxSteps: number | null;
}

export interface TaskDef {
  id: string;
  name: string;
  tags: string[];
  instruction: string;
  fingerprint: string;
  requirements: string;
}

export interface BenchmarkDef {
  id: string;
  name: string;
  version: string;
  summary: string;
  compatibility: string;
  canCreate: boolean;
  requirements: string;
  tasks: TaskDef[];
}

export interface ModelDef {
  id: string;
  label: string;
  alias: string | null;
  note: string;
}

export interface AgentDef {
  id: string;
  label: string;
  runtimeId: string;
  note: string;
}

export interface RuntimeDef {
  id: string;
  label: string;
  note: string;
}
