import { Link } from "react-router-dom";
import { benchmarkById } from "../data/catalog";
import { formatPercent, formatSeconds, modelLabel, STATUS_LABEL } from "../lib/format";
import { summarize } from "../lib/metrics";
import { useStudio } from "../state/Studio";
import { Empty, Metric, Page, StatusPill } from "../components/ui";

export function Workbench() {
  const { experiments, reset } = useStudio();
  const running = experiments.filter((experiment) => experiment.status === "running");
  const recent = experiments.filter((experiment) => experiment.status !== "draft").slice(0, 6);
  const attention = {
    env: experiments.reduce((total, experiment) => total + summarize(experiment).envErrors, 0),
    budget: experiments.reduce((total, experiment) => total + summarize(experiment).budget, 0),
    model: experiments.reduce((total, experiment) => total + summarize(experiment).modelFailures, 0),
  };
  const groups = [...new Set(experiments.map((experiment) => experiment.benchmarkId))];

  return (
    <Page
      kicker="工作台"
      title="最近的演示运行"
      lede="成功率按评测集分开，没有跨评测集总分。下面的数字都来自演示脚本。"
      actions={
        <button type="button" className="text-button" onClick={reset}>
          恢复初始演示数据
        </button>
      }
    >
      <section>
        <h2>需要关注</h2>
        <div className="link-row">
          <Link to="/traces?outcome=env_error">环境异常 {attention.env}</Link>
          <Link to="/traces?outcome=budget_exhausted">预算耗尽 {attention.budget}</Link>
          <Link to="/traces?outcome=wrong">模型失败 {attention.model}</Link>
        </div>
        <p className="fine">环境异常和预算耗尽不计入模型失败。取消也单独统计。</p>
      </section>
      <section>
        <h2>正在运行</h2>
        {running.length === 0 ? (
          <Empty title="当前没有运行中的演示实验。">
            <Link to="/experiments/new">新建实验</Link>
          </Empty>
        ) : (
          <ExperimentTable rows={running} />
        )}
      </section>
      <section>
        <h2>最近记录</h2>
        <ExperimentTable rows={recent} />
      </section>
      <section>
        <h2>按评测集</h2>
        {groups.map((id) => {
          const benchmark = benchmarkById(id);
          const rows = experiments.filter((experiment) => experiment.benchmarkId === id && experiment.samples.length > 0);
          return (
            <div key={id} className="block">
              <h3>{benchmark?.name ?? id}</h3>
              <p className="fine">范围：{benchmark?.version ?? "未记录"}。只统计这个评测集里的演示实验，不和其他评测集相加。</p>
              {rows.length === 0 ? <p className="fine">还没有样本。</p> : null}
              {rows.map((experiment) => {
                const summary = summarize(experiment);
                return (
                  <div key={experiment.id} className="metric-grid">
                    <Metric
                      label={`${experiment.name} · 成功率`}
                      value={summary.planned === 0 ? "未记录" : `${summary.correct}/${summary.planned}`}
                      note={`全样本 ${formatPercent(summary.successRate)}。分母是计划样本。未通过包含模型失败、环境异常、预算耗尽、取消和未结束。`}
                    />
                    <Metric
                      label="模型失败"
                      value={String(summary.modelFailures)}
                      note="只统计测试未通过。环境异常和预算耗尽不在这里。"
                    />
                    <Metric
                      label="输入 token"
                      value={summary.tokens.inputTokens == null ? (summary.tokens.costKind === "not_applicable" ? "不适用" : "未记录") : summary.tokens.inputTokens.toLocaleString("zh-CN")}
                      note={summary.tokens.cacheNote}
                    />
                    <Metric
                      label="墙钟时长"
                      value={formatSeconds(summary.time.wallClockS)}
                      note={summary.time.note}
                    />
                  </div>
                );
              })}
            </div>
          );
        })}
      </section>
    </Page>
  );
}

function ExperimentTable({ rows }: { rows: ReturnType<typeof useStudio>["experiments"] }) {
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>实验</th>
            <th>状态</th>
            <th>模型</th>
            <th>进度</th>
            <th className="num">成功率</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((experiment) => {
            const summary = summarize(experiment);
            return (
              <tr key={experiment.id}>
                <td>
                  <Link to={`/experiments/${experiment.id}`}>{experiment.name}</Link>
                  <div className="muted">{STATUS_LABEL[experiment.status]} · 演示</div>
                </td>
                <td>
                  <StatusPill value={experiment.status} />
                </td>
                <td>{modelLabel(experiment.modelId)}</td>
                <td>
                  {summary.progressed}/{summary.planned || "—"}
                  {experiment.status === "cancelled" ? " · 已停止" : ""}
                </td>
                <td className="num">{formatPercent(summary.successRate)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
