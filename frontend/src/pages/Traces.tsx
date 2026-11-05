import { useMemo, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { agents, benchmarks, models, taskById } from "../data/catalog";
import { Timeline } from "../components/Timeline";
import { Empty, Metric, Page, StatusPill, useNarrow } from "../components/ui";
import {
  agentLabel,
  cacheNote,
  formatSeconds,
  modelLabel,
  OUTCOME_LABEL,
  usageValue,
} from "../lib/format";
import { matchesQuery } from "../lib/text";
import { useExperiment, useStudio } from "../state/Studio";
import type { Experiment, Outcome, Sample } from "../types";

const OUTCOMES: Outcome[] = ["correct", "wrong", "budget_exhausted", "env_error", "cancelled", "running", "pending", "missing"];

export function TraceList() {
  const { experiments } = useStudio();
  const [params, setParams] = useSearchParams();
  const q = params.get("q") ?? "";
  const experimentId = params.get("experiment") ?? "";
  const model = params.get("model") ?? "";
  const agent = params.get("agent") ?? "";
  const benchmark = params.get("benchmark") ?? "";
  const outcome = params.get("outcome") ?? "";
  const reason = params.get("reason") ?? "";

  const rows = useMemo(() => {
    const list: { experiment: Experiment; sample: Sample }[] = [];
    for (const experiment of experiments) {
      if (experimentId && experiment.id !== experimentId) continue;
      if (model && experiment.modelId !== model) continue;
      if (agent && experiment.agentId !== agent) continue;
      if (benchmark && experiment.benchmarkId !== benchmark) continue;
      for (const sample of experiment.samples) {
        if (outcome && sample.outcome !== outcome) continue;
        if (reason && sample.endReason !== reason) continue;
        const blob = [
          sample.taskId,
          sample.endReason,
          sample.evidence,
          ...sample.steps.map((step) => `${step.command ?? ""} ${step.text}`),
        ].join("\n");
        if (!matchesQuery(blob, q)) continue;
        list.push({ experiment, sample });
      }
    }
    return list;
  }, [experiments, experimentId, model, agent, benchmark, outcome, reason, q]);

  const reasons = [...new Set(experiments.flatMap((experiment) => experiment.samples.map((sample) => sample.endReason)))];

  function setFilter(key: string, value: string) {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next);
  }

  return (
    <Page
      kicker="轨迹分析"
      title="跨实验轨迹"
      lede="这是独立入口。从实验详情打开的是同一条记录，筛选条件写在地址栏里。"
    >
      <div className="toolbar">
        <label>
          搜索命令或日志
          <input value={q} onChange={(event) => setFilter("q", event.target.value)} />
        </label>
        <label>
          实验
          <select value={experimentId} onChange={(event) => setFilter("experiment", event.target.value)}>
            <option value="">全部</option>
            {experiments.map((experiment) => (
              <option key={experiment.id} value={experiment.id}>
                {experiment.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          模型
          <select value={model} onChange={(event) => setFilter("model", event.target.value)}>
            <option value="">全部</option>
            {models.map((item) => (
              <option key={item.id} value={item.id}>
                {item.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          Agent
          <select value={agent} onChange={(event) => setFilter("agent", event.target.value)}>
            <option value="">全部</option>
            {agents.map((item) => (
              <option key={item.id} value={item.id}>
                {item.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          评测集
          <select value={benchmark} onChange={(event) => setFilter("benchmark", event.target.value)}>
            <option value="">全部</option>
            {benchmarks.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          结果
          <select value={outcome} onChange={(event) => setFilter("outcome", event.target.value)}>
            <option value="">全部</option>
            {OUTCOMES.map((item) => (
              <option key={item} value={item}>
                {OUTCOME_LABEL[item]}
              </option>
            ))}
          </select>
        </label>
        <label>
          结束原因
          <select value={reason} onChange={(event) => setFilter("reason", event.target.value)}>
            <option value="">全部</option>
            {reasons.map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
        </label>
        <button type="button" className="text-button" onClick={() => setParams(new URLSearchParams())}>
          清除筛选
        </button>
      </div>
      {rows.length === 0 ? (
        <Empty title="没有符合条件的轨迹。">
          <button type="button" onClick={() => setParams(new URLSearchParams())}>
            清除筛选
          </button>
        </Empty>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>任务</th>
                <th>实验</th>
                <th>模型 / Agent</th>
                <th>结果</th>
                <th>结束原因</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(({ experiment, sample }) => (
                <tr key={`${experiment.id}-${sample.id}`}>
                  <td>
                    <Link to={tracePath(experiment.id, sample.id)}>{taskById(sample.taskId)?.name ?? sample.taskId}</Link>
                    <div className="muted">第 {sample.repeat + 1} 次</div>
                  </td>
                  <td>{experiment.name}</td>
                  <td>
                    {modelLabel(experiment.modelId)}
                    <div className="muted">{agentLabel(experiment.agentId)}</div>
                  </td>
                  <td>
                    <StatusPill value={sample.outcome} />
                  </td>
                  <td>{sample.endReason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Page>
  );
}

export function TraceDetail() {
  const { experimentId, sampleId: rawSampleId } = useParams();
  const sampleId = rawSampleId ? decodeURIComponent(rawSampleId) : "";
  const experiment = useExperiment(experimentId);
  const sample = experiment?.samples.find((item) => item.id === sampleId);
  const [params, setParams] = useSearchParams();
  const [expanded, setExpanded] = useState(false);
  const [tab, setTab] = useState<"task" | "timeline" | "score">("timeline");
  const narrow = useNarrow();
  const navigate = useNavigate();
  const query = params.get("q") ?? "";
  const { experiments } = useStudio();
  if (!experiment || !sample) {
    return (
      <Page kicker="轨迹分析" title="没有这条轨迹">
        <Link to="/traces">返回轨迹列表</Link>
      </Page>
    );
  }
  const task = taskById(sample.taskId);
  const peers = experiments.flatMap((item) =>
    item.samples
      .filter((other) => other.taskId === sample.taskId && !(item.id === experiment.id && other.id === sample.id))
      .map((other) => ({ experiment: item, sample: other })),
  );
  const hits = sample.steps.filter((step) =>
    matchesQuery(`${step.command ?? ""} ${step.text}`, query),
  ).length;

  function locate() {
    setTab("timeline");
    window.setTimeout(() => {
      document.querySelector("[data-failure-cause='true']")?.scrollIntoView({ block: "center" });
    }, 0);
  }

  const back =
    params.get("from") === "experiment" ? `/experiments/${experiment.id}` : `/traces?experiment=${experiment.id}`;

  return (
    <Page
      kicker="轨迹分析 · 演示记录"
      title={task?.name ?? sample.taskId}
      lede="沿时间线查看行动，打开独立记录核对完整证据。"
      actions={
        <>
          <Link className="button secondary" to={back}>
            {params.get("from") === "experiment" ? "返回实验" : "返回列表"}
          </Link>
          <Link className="button secondary" to={`/traces?q=${encodeURIComponent(sample.taskId)}`}>
            在轨迹列表中查看
          </Link>
        </>
      }
    >
      <div className="toolbar">
        <label>
          搜索
          <input
            value={query}
            onChange={(event) => {
              const next = new URLSearchParams(params);
              if (event.target.value) next.set("q", event.target.value);
              else next.delete("q");
              setParams(next, { replace: true });
            }}
          />
        </label>
        <span className="fine">{query.trim() ? `${hits} 处` : "可搜索命令和日志"}</span>
        <button type="button" onClick={locate}>
          定位失败原因
        </button>
        <button type="button" onClick={() => setExpanded((value) => !value)}>
          {expanded ? "折叠长日志" : "展开长日志"}
        </button>
        <label>
          同一任务对照
          <select
            defaultValue=""
            onChange={(event) => {
              if (!event.target.value) return;
              const [otherExp, otherSample] = event.target.value.split("|");
              navigate(
                `/compare/traces?leftExp=${experiment.id}&leftSample=${encodeURIComponent(sample.id)}&rightExp=${otherExp}&rightSample=${encodeURIComponent(otherSample ?? "")}`,
              );
            }}
          >
            <option value="">选择另一条</option>
            {peers.map((peer) => (
              <option key={`${peer.experiment.id}-${peer.sample.id}`} value={`${peer.experiment.id}|${peer.sample.id}`}>
                {peer.experiment.name} · {OUTCOME_LABEL[peer.sample.outcome]}
              </option>
            ))}
          </select>
        </label>
      </div>
      {narrow ? (
        <div className="tabs" role="tablist" aria-label="轨迹分区">
          {(
            [
              ["task", "任务"],
              ["timeline", "时间线"],
              ["score", "评分"],
            ] as const
          ).map(([id, label]) => (
            <button key={id} type="button" role="tab" aria-selected={tab === id} onClick={() => setTab(id)}>
              {label}
            </button>
          ))}
        </div>
      ) : null}
      <div className="trace-grid">
        {(!narrow || tab === "task") && (
          <aside className="pane">
            <h2>任务</h2>
            <p>{task?.instruction ?? "未记录任务说明。"}</p>
            <p className="fine">指纹 {sample.taskFingerprint}</p>
            <h2>步骤</h2>
            <ol className="step-nav">
              {sample.steps.map((step, index) => (
                <li key={step.id}>
                  <button
                    type="button"
                    onClick={() => {
                      setTab("timeline");
                      window.setTimeout(() => {
                        document.getElementById(`step-${step.id}`)?.scrollIntoView({ block: "center" });
                      }, 0);
                    }}
                  >
                    {index + 1}. {step.kind === "tool_call" ? step.tool ?? "工具" : step.actor === "model" ? "模型" : step.actor === "environment" ? "环境" : "记录"}
                    {step.failureCause ? " · 失败原因" : step.error ? " · 错误" : ""}
                  </button>
                </li>
              ))}
            </ol>
            {sample.steps.length === 0 ? <p className="fine">还没有步骤。</p> : null}
          </aside>
        )}
        {(!narrow || tab === "timeline") && (
          <section className="pane">
            <h2>时间线</h2>
            <Timeline steps={sample.steps} query={query} expanded={expanded} recordBase={tracePath(experiment.id, sample.id)} />
          </section>
        )}
        {(!narrow || tab === "score") && (
          <aside className="pane">
            <h2>评分证据</h2><div className="record-links"><Link to={`${tracePath(experiment.id, sample.id)}/evidence`}>完整证据 →</Link><Link target="_blank" rel="noopener noreferrer" to={`${tracePath(experiment.id, sample.id)}/evidence`}>新标签页 ↗</Link></div>
            <p>{sample.evidence || "未记录"}</p>
            <p>
              结束原因：{sample.endReason} <StatusPill value={sample.outcome} />
            </p>
            <Metric
              label="分数"
              value={sample.outcome === "env_error" ? "不适用" : sample.score == null ? "未记录" : String(sample.score)}
              note="单题分数。实验成功率的分母仍是计划样本。"
            />
            <Metric label="耗时" value={formatSeconds(sample.wallTimeS, sample.outcome === "env_error" ? "不适用" : "未记录")} note="这一题的墙钟，不是逐步耗时之和。" />
            <Metric label="输入 token" value={usageValue(sample.usage, "inputTokens")} note={cacheNote(sample.usage)} />
            <Metric label="输出 token" value={usageValue(sample.usage, "outputTokens")} note="最终汇总。逐步 token 都是未记录，不能把汇总额倒推成每一步。" />
            <Metric label="缓存 token" value={usageValue(sample.usage, "cachedTokens")} note={cacheNote(sample.usage)} />
            <p className="fine">
              {modelLabel(experiment.modelId)} · {agentLabel(experiment.agentId)} · {experiment.name}
            </p>
          </aside>
        )}
      </div>
    </Page>
  );
}

export function TracePair() {
  const [params] = useSearchParams();
  const { experiments } = useStudio();
  const left = findSample(experiments, params.get("leftExp"), params.get("leftSample"));
  const right = findSample(experiments, params.get("rightExp"), params.get("rightSample"));
  if (!left || !right) {
    return (
      <Page kicker="轨迹分析" title="缺少对照轨迹">
        <Link to="/compare">返回实验对比</Link>
      </Page>
    );
  }
  return (
    <Page
      kicker="轨迹对照"
      title={taskById(left.sample.taskId)?.name ?? left.sample.taskId}
      lede="左右是同一任务的两条演示记录。环境异常和预算耗尽不会被说成模型答错。"
    >
      <div className="pair-grid">
        {[left, right].map((side) => (
          <section key={side.experiment.id + side.sample.id} className="pane">
            <h2>{side.experiment.name}</h2>
            <p>
              <StatusPill value={side.sample.outcome} /> {side.sample.endReason}
            </p>
            <p>{side.sample.evidence || "未记录评分证据。"}</p>
            <p className="fine">
              输入 {usageValue(side.sample.usage, "inputTokens")} · 输出 {usageValue(side.sample.usage, "outputTokens")} · 耗时{" "}
              {formatSeconds(side.sample.wallTimeS)}
            </p>
            <Timeline steps={side.sample.steps} query="" expanded={false} />
            <Link to={tracePath(side.experiment.id, side.sample.id)}>打开完整三栏</Link>
          </section>
        ))}
      </div>
    </Page>
  );
}

function findSample(experiments: Experiment[], experimentId: string | null, sampleId: string | null) {
  const experiment = experiments.find((item) => item.id === experimentId);
  const sample = experiment?.samples.find((item) => item.id === sampleId);
  if (!experiment || !sample) return null;
  return { experiment, sample };
}

export function tracePath(experimentId: string, sampleId: string): string {
  return `/traces/${experimentId}/${encodeURIComponent(sampleId)}`;
}
