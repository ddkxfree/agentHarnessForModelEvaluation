import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { agents, benchmarks, models } from "../data/catalog";
import { formatPercent, modelLabel, agentLabel } from "../lib/format";
import { summarize } from "../lib/metrics";
import { useStudio } from "../state/Studio";
import { Empty, Page, StatusPill } from "../components/ui";
import type { ExperimentStatus } from "../types";

export function ExperimentList() {
  const { experiments } = useStudio();
  const [params, setParams] = useSearchParams();
  const [picked, setPicked] = useState<string[]>([]);
  const [limitNote, setLimitNote] = useState<string | null>(null);
  const q = params.get("q") ?? "";
  const status = params.get("status") ?? "";
  const model = params.get("model") ?? "";
  const agent = params.get("agent") ?? "";
  const benchmark = params.get("benchmark") ?? "";

  const rows = useMemo(() => {
    return experiments.filter((experiment) => {
      if (status && experiment.status !== status) return false;
      if (model && experiment.modelId !== model) return false;
      if (agent && experiment.agentId !== agent) return false;
      if (benchmark && experiment.benchmarkId !== benchmark) return false;
      const hay = `${experiment.name} ${experiment.id}`.toLowerCase();
      return hay.includes(q.trim().toLowerCase());
    });
  }, [experiments, q, status, model, agent, benchmark]);

  function setFilter(key: string, value: string) {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next);
  }

  function toggle(id: string) {
    setLimitNote(null);
    setPicked((current) => {
      if (current.includes(id)) return current.filter((item) => item !== id);
      if (current.length >= 2) {
        setLimitNote("一次只能选两个实验。");
        return current;
      }
      return [...current, id];
    });
  }

  return (
    <Page
      kicker="实验中心"
      title="实验列表"
      lede="搜索和筛选写在地址栏里，刷新或返回会保持。对比只带你去看差异，不会发起运行。"
      actions={<Link className="button" to="/experiments/new">新建实验</Link>}
    >
      <div className="toolbar">
        <label>
          搜索
          <input value={q} onChange={(event) => setFilter("q", event.target.value)} />
        </label>
        <label>
          状态
          <select value={status} onChange={(event) => setFilter("status", event.target.value)}>
            <option value="">全部</option>
            {(["draft", "running", "completed", "cancelled"] as ExperimentStatus[]).map((item) => (
              <option key={item} value={item}>
                {item === "draft" ? "草稿" : item === "running" ? "运行中" : item === "completed" ? "已完成" : "已取消"}
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
        <button type="button" className="text-button" onClick={() => setParams(new URLSearchParams())}>
          清除筛选
        </button>
      </div>
      <div className="row-actions">
        {picked.length === 2 ? (
          <Link className="button" to={`/compare?a=${picked[0]}&b=${picked[1]}`}>
            对比所选
          </Link>
        ) : (
          <button type="button" disabled>
            对比所选
          </button>
        )}
        <span className="fine">{picked.length === 2 ? "已选两个实验。" : "选择两个实验后才能对比。"}</span>
        {limitNote ? <span className="fine">{limitNote}</span> : null}
      </div>
      {rows.length === 0 ? (
        <Empty title="没有符合筛选的实验。">
          <button type="button" onClick={() => setParams(new URLSearchParams())}>
            清除筛选
          </button>
        </Empty>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>对比</th>
                <th>实验</th>
                <th>状态</th>
                <th>模型 / Agent</th>
                <th className="num">成功率</th>
                <th className="num">进度</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((experiment) => {
                const summary = summarize(experiment);
                return (
                  <tr key={experiment.id}>
                    <td>
                      <input
                        type="checkbox"
                        aria-label={`选择 ${experiment.name}`}
                        checked={picked.includes(experiment.id)}
                        onChange={() => toggle(experiment.id)}
                      />
                    </td>
                    <td>
                      <Link to={`/experiments/${experiment.id}`}>{experiment.name}</Link>
                    </td>
                    <td>
                      <StatusPill value={experiment.status} />
                    </td>
                    <td>
                      {modelLabel(experiment.modelId)}
                      <div className="muted">{agentLabel(experiment.agentId)}</div>
                    </td>
                    <td className="num">{summary.planned ? formatPercent(summary.successRate) : "未记录"}</td>
                    <td className="num">
                      {summary.progressed}/{summary.planned || "—"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </Page>
  );
}
