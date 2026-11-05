import { useMemo } from "react";
import { useSearchParams } from "react-router-dom";
import { benchmarkById } from "../data/catalog";
import { ExperimentForm } from "../components/ExperimentForm";
import { Page } from "../components/ui";
import type { DraftInput } from "../types";

export function NewExperiment() {
  const [params] = useSearchParams();
  const benchmarkId = params.get("benchmark") ?? "terminal-bench";
  const benchmark = benchmarkById(benchmarkId);
  const requested = (params.get("tasks") ?? "").split(",").filter(Boolean);
  const taskIds = useMemo(() => {
    if (!benchmark?.canCreate) return [];
    const known = requested.filter((id) => benchmark.tasks.some((task) => task.id === id));
    return known.length > 0 ? known : benchmark.tasks.map((task) => task.id);
  }, [benchmark, requested]);

  const initial: DraftInput = {
    name: benchmark ? `${benchmark.name} 演示` : "演示实验",
    benchmarkId: benchmark?.canCreate ? benchmark.id : "terminal-bench",
    taskIds: benchmark?.canCreate ? taskIds : (benchmarkById("terminal-bench")?.tasks.map((task) => task.id) ?? []),
    modelId: "deepseek-flash",
    agentId: "basic",
    runtimeId: "native",
    repeats: 1,
    concurrency: 1,
    maxTokens: 30_000,
    maxSteps: null,
  };

  return (
    <Page
      kicker="实验中心"
      title="新建实验"
      lede="自然语言只解析写明的例子，并填进可以修改的表单。创建的是演示草稿，不会调用模型。"
    >
      <ExperimentForm key={`${initial.benchmarkId}:${initial.taskIds.join(",")}`} initial={initial} />
    </Page>
  );
}
