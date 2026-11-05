import { Link } from "react-router-dom";
import { formatPercent, formatSeconds, modelLabel, STATUS_LABEL } from "../lib/format";
import { summarize } from "../lib/metrics";
import { useStudio } from "../state/Studio";
import { Empty, Metric, Page, StatusPill } from "../components/ui";

export function Workbench() {
  const { experiments } = useStudio();
  const running = experiments.filter((experiment) => experiment.status === "running");
  const recent = experiments.filter((experiment) => experiment.status !== "draft").slice(0, 6);
  const attention = {
    env: experiments.reduce((total, experiment) => total + summarize(experiment).envErrors, 0),
    budget: experiments.reduce((total, experiment) => total + summarize(experiment).budget, 0),
    model: experiments.reduce((total, experiment) => total + summarize(experiment).modelFailures, 0),
  };
  const latest = recent[0];
  const summary = latest ? summarize(latest) : null;

  return (
    <Page
      kicker="工作台"
      title="每一次实验，都有据可循"
      lede="关注正在发生的变化，沿着报告与轨迹深入查看。"
      actions={<><Link className="button secondary" to="/compare">对比实验</Link><Link className="button" to="/experiments/new">＋ 新建实验</Link></>}
    >
      <div className="metric-grid overview-metrics">
        <Metric label="实验记录" value={String(experiments.length)} note="当前浏览器的演示空间" />
        <Metric label="正在运行" value={String(running.length)} note="模拟执行中的实验" />
        <Metric label="最近实验成功率" value={summary ? formatPercent(summary.successRate) : "未记录"} note={latest ? latest.name : "创建实验开始探索"} />
        <Metric label="最近实验耗时" value={summary ? formatSeconds(summary.time.wallClockS) : "未记录"} note="墙钟时长 · 单次实验" />
      </div>
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
      <div className="report-links"><span>详细配置、统计与评分证据，保存在每条实验的完整报告中。</span><Link to="/help/metrics">了解统计口径 →</Link></div>
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
            <th className="num">成功率</th><th>报告</th>
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
                <td className="num">{formatPercent(summary.successRate)}</td><td><Link to={`/experiments/${experiment.id}/report`}>查看完整报告 →</Link><div className="muted"><Link target="_blank" rel="noopener noreferrer" to={`/experiments/${experiment.id}/report`}>新标签页 ↗</Link></div></td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
