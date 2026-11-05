import { Link } from "react-router-dom";
import { useParams } from "react-router-dom";
import { taskById } from "../data/catalog";
import { ExperimentForm } from "../components/ExperimentForm";
import { Empty, Metric, Notice, Page, StatusPill } from "../components/ui";
import {
  agentLabel,
  formatInt,
  formatPercent,
  formatSeconds,
  formatWhen,
  limitLabel,
  modelLabel,
  runtimeLabel,
  STEP_DEFINITION,
  usageValue,
} from "../lib/format";
import { summarize } from "../lib/metrics";
import { useExperiment, useStudio } from "../state/Studio";
import type { DraftInput, Outcome } from "../types";

export function ExperimentDetail({ report = false }: { report?: boolean }) {
  const { experimentId } = useParams();
  const experiment = useExperiment(experimentId);
  const { start, cancel } = useStudio();
  if (!experiment) {
    return (
      <Page kicker="实验中心" title="没有这个实验">
        <Link to="/experiments">返回列表</Link>
      </Page>
    );
  }
  const summary = summarize(experiment);
  const draft: DraftInput = {
    name: experiment.name,
    benchmarkId: experiment.benchmarkId,
    taskIds: experiment.taskIds,
    modelId: experiment.modelId,
    agentId: experiment.agentId,
    runtimeId: experiment.runtimeId,
    repeats: experiment.repeats,
    concurrency: experiment.concurrency,
    maxTokens: experiment.maxTokens,
    maxSteps: experiment.maxSteps,
  };
  const outcomeOrder: Outcome[] = ["correct", "wrong", "env_error", "budget_exhausted", "cancelled", "running", "pending", "missing"];
  const maxCount = Math.max(1, ...outcomeOrder.map((outcome) => experiment.samples.filter((sample) => sample.outcome === outcome).length));

  return (
    <Page
      kicker="实验中心 · 演示"
      title={report ? `${experiment.name} · 完整报告` : experiment.name}
      lede="进度、样本和用量来自同一份演示状态。回放轨迹不会恢复执行现场。"
      actions={
        <>
          {!report && experiment.status === "draft" ? (
            <button type="button" onClick={() => start(experiment.id)}>
              开始模拟运行
            </button>
          ) : null}
          {!report && experiment.status === "running" ? (
            <button type="button" onClick={() => cancel(experiment.id)}>
              取消
            </button>
          ) : null}
          {!report && experiment.status === "cancelled" ? (
            <button type="button" disabled>
              已取消，不能续跑
            </button>
          ) : null}
          <Link to={report ? `/experiments/${experiment.id}` : `/experiments/${experiment.id}/report`}>{report ? "← 返回实验" : "查看完整报告 →"}</Link>
          <Link target="_blank" rel="noopener noreferrer" to={`/experiments/${experiment.id}/report`}>新标签页打开 ↗</Link>
          <Link className="button secondary" to={`/compare?a=${experiment.id}`}>
            去对比
          </Link>
        </>
      }
    >
      {summary.warning ? <Notice>{summary.warning}</Notice> : null}
      <p className="fine"><Link to="/help/metrics">指标定义与统计口径 ↗</Link></p>
      {report ? <>
      <dl className="facts">
        <div>
          <dt>评测集</dt>
          <dd>
            <Link to={`/benchmarks/${experiment.benchmarkId}`}>{experiment.benchmarkId}</Link>
            <span> {experiment.benchmarkVersion}</span>
          </dd>
        </div>
        <div>
          <dt>模型</dt>
          <dd>{modelLabel(experiment.modelId)}</dd>
        </div>
        <div>
          <dt>Agent</dt>
          <dd>{agentLabel(experiment.agentId)}</dd>
        </div>
        <div>
          <dt>执行环境</dt>
          <dd>{runtimeLabel(experiment.runtimeId)}</dd>
        </div>
        <div>
          <dt>重复 / 并发</dt>
          <dd>
            {experiment.repeats} / {experiment.concurrency}
          </dd>
        </div>
        <div>
          <dt>token 上限</dt>
          <dd>{limitLabel(experiment.maxTokens, "tokens")}</dd>
        </div>
        <div>
          <dt>步数上限</dt>
          <dd>{limitLabel(experiment.maxSteps, "步")}</dd>
        </div>
        <div>
          <dt>开始</dt>
          <dd>{formatWhen(experiment.startedAt)}</dd>
        </div>
      </dl>
      <div className="metric-grid">
        <Metric
          label="状态"
          value={experiment.status === "running" ? "运行中" : experiment.status === "cancelled" ? "已取消" : experiment.status === "draft" ? "草稿" : "已完成"}
          note={experiment.status === "cancelled" ? "计时已停止。" : "演示状态，不是真实集群状态。"}
        />
        <Metric
          label="成功率"
          value={summary.planned ? `${summary.correct}/${summary.planned}` : "未记录"}
          note={`全样本 ${formatPercent(summary.successRate)}。分母 ${summary.planned}。模型失败 ${summary.modelFailures}，环境异常 ${summary.envErrors}，预算耗尽 ${summary.budget}，取消 ${summary.cancelled}。`}
        />
        <Metric
          label="输入 / 输出 / 缓存"
          value={`${usageTotal(summary.tokens.inputTokens, summary.tokens.costKind)} / ${usageTotal(summary.tokens.outputTokens, summary.tokens.costKind)} / ${usageTotal(summary.tokens.cachedTokens, summary.tokens.costKind)}`}
          note={summary.tokens.cacheNote}
        />
        <Metric
          label="费用"
          value={
            summary.tokens.costKind === "not_applicable"
              ? "不适用"
              : summary.tokens.costCny == null
                ? "未记录"
                : `估算 ${summary.tokens.costCny.toFixed(4)} 元`
          }
          note={summary.tokens.costNote}
        />
        <Metric label="墙钟时长" value={formatSeconds(summary.time.wallClockS)} note={summary.time.note} />
        <Metric
          label="任务耗时之和"
          value={formatSeconds(summary.time.taskSeconds)}
          note="只在已结束样本都有耗时时合计。"
        />
        <Metric
          label="步数 / 工具调用"
          value={`${summary.steps} / ${summary.toolCalls}`}
          note={STEP_DEFINITION}
        />
      </div>
      </> : <div className="metric-grid overview-metrics"><Metric label="成功率" value={formatPercent(summary.successRate)} note={`${summary.correct} / ${summary.planned} 计划样本`} /><Metric label="输入 token" value={usageTotal(summary.tokens.inputTokens, summary.tokens.costKind)} note={modelLabel(experiment.modelId)} /><Metric label="墙钟时长" value={formatSeconds(summary.time.wallClockS)} note="本次实验" /><Metric label="环境异常 / 预算耗尽" value={`${summary.envErrors} / ${summary.budget}`} note="与模型失败分别统计" /></div>}
      <section>
        <h2>进度</h2>
        <p className="progress-label" aria-live="polite">
          {experiment.status === "cancelled" ? "已取消，停止在" : "进度"} {summary.progressed}/{summary.planned || "—"}
        </p>
        <div className="track" aria-hidden="true">
          <span
            style={{
              width: summary.planned ? `${(summary.progressed / summary.planned) * 100}%` : "0%",
            }}
          />
        </div>
        <div className="bars" role="img" aria-label="各结果的样本数">
          {outcomeOrder.map((outcome) => {
            const count = experiment.samples.filter((sample) => sample.outcome === outcome).length;
            if (count === 0) return null;
            return (
              <div key={outcome} className="bar-row">
                <span>{outcomeLabel(outcome)}</span>
                <span className="bar-track">
                  <span data-state={outcome} style={{ width: `${(count / maxCount) * 100}%` }} />
                </span>
                <span className="num">{count}</span>
              </div>
            );
          })}
        </div>
        <p className="fine">单位：样本。范围：本实验计划样本 {summary.planned}。颜色和文字一起表示结果，没有置信区间。</p>
      </section>
      {report ? <section>
        <h2>事件</h2>
        <ul className="events">
          {experiment.events.map((item) => (
            <li key={item.id}>
              <time>{formatWhen(item.at)}</time>
              <span>{item.text}</span>
            </li>
          ))}
        </ul>
      </section>
      : null}
      <section>
        <h2>样本</h2>
        {experiment.samples.length === 0 ? (
          <Empty title="还没有样本。">开始模拟运行后才会按任务和重复次数生成。</Empty>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>任务</th>
                  <th>重复</th>
                  <th>结果</th>
                  <th>结束原因</th>
                  <th className="num">分数</th>
                  <th className="num">耗时</th>
                  <th className="num">输入</th>
                  <th>轨迹</th>
                </tr>
              </thead>
              <tbody>
                {experiment.samples.map((sample) => (
                  <tr key={sample.id}>
                    <td>{taskById(sample.taskId)?.name ?? sample.taskId}</td>
                    <td>{sample.repeat + 1}</td>
                    <td>
                      <StatusPill value={sample.outcome} />
                    </td>
                    <td>{sample.endReason}</td>
                    <td className="num">{scoreText(sample.score, sample.outcome)}</td>
                    <td className="num">{formatSeconds(sample.wallTimeS, sample.outcome === "env_error" ? "不适用" : "未记录")}</td>
                    <td className="num">{usageValue(sample.usage, "inputTokens")}</td>
                    <td>
                      <Link to={`/traces/${experiment.id}/${encodeURIComponent(sample.id)}?from=experiment`}>打开</Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
      {!report && experiment.status === "draft" ? (
        <section>
          <h2>修改配置</h2>
          <ExperimentForm experiment={experiment} initial={draft} />
        </section>
      ) : null}
    </Page>
  );
}

function usageTotal(value: number | null, kind: string): string {
  if (kind === "not_applicable") return "不适用";
  return formatInt(value);
}

function scoreText(score: number | null, outcome: string): string {
  if (outcome === "env_error") return "不适用";
  if (score == null) return "未记录";
  return String(score);
}

function outcomeLabel(outcome: Outcome): string {
  const labels: Record<Outcome, string> = {
    correct: "通过",
    wrong: "模型失败",
    budget_exhausted: "预算耗尽",
    env_error: "环境异常",
    cancelled: "取消",
    running: "进行中",
    pending: "未开始",
    missing: "缺失",
  };
  return labels[outcome];
}
