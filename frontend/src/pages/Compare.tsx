import { Link, useSearchParams } from "react-router-dom";
import { formatPercent, formatPoints, formatSeconds, modelLabel } from "../lib/format";
import { compareExperiments, summarize } from "../lib/metrics";
import { useStudio } from "../state/Studio";
import { Empty, Notice, Page } from "../components/ui";
import { tracePath } from "./Traces";
import type { HeldOut, PairItem } from "../lib/metrics";

const HELD_LABEL: Record<HeldOut["reason"], string> = {
  env: "环境异常",
  cancelled: "取消",
  budget: "预算耗尽",
  unfinished: "未结束",
  missing: "缺失",
  identity: "题目指纹不同",
  unmatched: "只在一侧",
};

export function Compare() {
  const { experiments } = useStudio();
  const [params, setParams] = useSearchParams();
  const fallbackLeft = experiments.find((item) => item.id === "exp-ds-basic-30k")?.id ?? experiments[0]?.id ?? "";
  const fallbackRight = experiments.find((item) => item.id === "exp-qwen-basic-30k")?.id ?? experiments[1]?.id ?? "";
  const leftId = params.get("a") ?? fallbackLeft;
  const requestedRight = params.get("b") ?? fallbackRight;
  const rightId = requestedRight === leftId ? (leftId === fallbackRight ? fallbackLeft : fallbackRight) : requestedRight;
  const left = experiments.find((item) => item.id === leftId);
  const right = experiments.find((item) => item.id === rightId);

  function choose(key: "a" | "b", value: string) {
    const next = new URLSearchParams(params);
    next.set("a", key === "a" ? value : leftId);
    next.set("b", key === "b" ? value : rightId);
    setParams(next);
  }

  const comparison = left && right && left.id !== right.id ? compareExperiments(left, right) : null;
  const leftSummary = left ? summarize(left) : null;
  const rightSummary = right ? summarize(right) : null;

  return (
    <Page
      kicker="实验对比"
      title="两次运行的差异"
      lede="成功率之差用百分点。任务集、Agent 或预算不同时，页面会写出具体差异，不会把变化都算到模型上。"
      actions={
        <button type="button" disabled>
          导出公开榜
        </button>
      }
    >
      <p className="fine">导出公开榜未实现。公开成绩需要单独的数据许可和发布授权，原型不发布。</p>
      <div className="toolbar">
        <label>
          左侧
          <select value={leftId} onChange={(event) => choose("a", event.target.value)}>
            {experiments.map((experiment) => (
              <option key={experiment.id} value={experiment.id}>
                {experiment.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          右侧
          <select value={rightId} onChange={(event) => choose("b", event.target.value)}>
            {experiments.map((experiment) => (
              <option key={experiment.id} value={experiment.id}>
                {experiment.name}
              </option>
            ))}
          </select>
        </label>
      </div>
      {!left || !right || left.id === right.id ? (
        <Empty title="请选择两个不同的实验。" />
      ) : null}
      {comparison && left && right && leftSummary && rightSummary ? (
        <>
          <Notice>{comparison.notes.join(" ")}</Notice>
          <section>
            <h2>配置差异</h2>
            {comparison.configChanges.length === 0 ? (
              <p>没有配置字段差异。</p>
            ) : (
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>字段</th>
                      <th>左侧</th>
                      <th>右侧</th>
                    </tr>
                  </thead>
                  <tbody>
                    {comparison.configChanges.map((row) => (
                      <tr key={row.key}>
                        <td>{row.key}</td>
                        <td>{row.left}</td>
                        <td>{row.right}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
          <section>
            <h2>结果与消耗</h2>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>指标</th>
                    <th>左侧 · {left.name}</th>
                    <th>右侧 · {right.name}</th>
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <td>成功率（全样本）</td>
                    <td>
                      {leftSummary.correct}/{leftSummary.planned} · {formatPercent(comparison.leftRate)}
                    </td>
                    <td>
                      {rightSummary.correct}/{rightSummary.planned} · {formatPercent(comparison.rightRate)}
                      <div className="muted">相对左侧 {formatPoints(comparison.percentagePoints)}</div>
                    </td>
                  </tr>
                  <tr>
                    <td>模型失败 / 环境异常 / 预算耗尽</td>
                    <td>
                      {leftSummary.modelFailures} / {leftSummary.envErrors} / {leftSummary.budget}
                    </td>
                    <td>
                      {rightSummary.modelFailures} / {rightSummary.envErrors} / {rightSummary.budget}
                    </td>
                  </tr>
                  <tr>
                    <td>输入 token</td>
                    <td>{tokenCell(leftSummary.tokens.inputTokens, comparison.tokensComparable)}</td>
                    <td>{tokenCell(rightSummary.tokens.inputTokens, comparison.tokensComparable)}</td>
                  </tr>
                  <tr>
                    <td>输出 token</td>
                    <td>{tokenCell(leftSummary.tokens.outputTokens, comparison.tokensComparable)}</td>
                    <td>{tokenCell(rightSummary.tokens.outputTokens, comparison.tokensComparable)}</td>
                  </tr>
                  <tr>
                    <td>费用估算</td>
                    <td>{costCell(leftSummary.tokens.costCny, leftSummary.tokens.costKind, comparison.tokensComparable)}</td>
                    <td>{costCell(rightSummary.tokens.costCny, rightSummary.tokens.costKind, comparison.tokensComparable)}</td>
                  </tr>
                  <tr>
                    <td>墙钟时长</td>
                    <td>{formatSeconds(leftSummary.time.wallClockS)}</td>
                    <td>{formatSeconds(rightSummary.time.wallClockS)}</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <p className="fine">
              样本量：左侧 {leftSummary.planned}，右侧 {rightSummary.planned}。模型 {modelLabel(left.modelId)} 与{" "}
              {modelLabel(right.modelId)}。没有统计检验，也不显示置信区间。
              {comparison.tokensComparable ? "" : " 用量不能比较时，不把缺失显示成 0。"}
            </p>
          </section>
          <section>
            <h2>能力标签</h2>
            <p className="fine">样本数是带这个标签的计划样本。通过数只数结果为通过的样本。</p>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>标签</th>
                    <th className="num">左侧通过 / 样本</th>
                    <th className="num">右侧通过 / 样本</th>
                  </tr>
                </thead>
                <tbody>
                  {comparison.capability.map((row) => (
                    <tr key={row.tag}>
                      <td>{row.tag}</td>
                      <td className="num">
                        {row.leftCorrect}/{row.leftN}
                      </td>
                      <td className="num">
                        {row.rightCorrect}/{row.rightN}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
          {comparison.sameBenchmark ? (
            <section>
              <h2>配对样本</h2>
              <PairLane title="失败 → 成功" items={comparison.buckets.failSuccess} leftId={left.id} rightId={right.id} />
              <PairLane title="成功 → 失败" items={comparison.buckets.successFail} leftId={left.id} rightId={right.id} />
              <PairLane title="都成功" items={comparison.buckets.bothSuccess} leftId={left.id} rightId={right.id} />
              <PairLane title="都失败" items={comparison.buckets.bothFail} leftId={left.id} rightId={right.id} />
              <h2>不归入模型答错</h2>
              {comparison.held.length === 0 ? <p>没有需要单独列出的样本。</p> : null}
              <ul className="held">
                {comparison.held.map((item) => (
                  <li key={`${item.reason}-${item.taskId}-${item.repeat}`}>
                    <strong>{HELD_LABEL[item.reason]}</strong> {item.taskId} 第 {item.repeat + 1} 次。{item.detail}
                  </li>
                ))}
              </ul>
            </section>
          ) : (
            <Empty title="评测集不同，没有逐题配对。" />
          )}
        </>
      ) : null}
    </Page>
  );
}

function PairLane({
  title,
  items,
  leftId,
  rightId,
}: {
  title: string;
  items: PairItem[];
  leftId: string;
  rightId: string;
}) {
  return (
    <div className="block">
      <h3>
        {title} · {items.length}
      </h3>
      {items.length === 0 ? <p className="fine">这一组是空的。</p> : null}
      <ul className="held">
        {items.map((item) => (
          <li key={`${item.taskId}-${item.repeat}`}>
            {item.taskId} 第 {item.repeat + 1} 次{" "}
            <Link to={tracePath(leftId, item.left.id)}>左侧轨迹</Link>
            {" · "}
            <Link to={tracePath(rightId, item.right.id)}>右侧轨迹</Link>
            {" · "}
            <Link
              to={`/compare/traces?leftExp=${leftId}&leftSample=${encodeURIComponent(item.left.id)}&rightExp=${rightId}&rightSample=${encodeURIComponent(item.right.id)}`}
            >
              左右对照
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}

function tokenCell(value: number | null, comparable: boolean): string {
  if (!comparable) return value == null ? "未记录，不能比较" : `${value.toLocaleString("zh-CN")}（覆盖不足，不作比较）`;
  if (value == null) return "未记录";
  return value.toLocaleString("zh-CN");
}

function costCell(value: number | null, kind: string, comparable: boolean): string {
  if (kind === "not_applicable") return "不适用";
  if (!comparable || value == null) return "未记录";
  return `估算 ${value.toFixed(4)} 元`;
}
