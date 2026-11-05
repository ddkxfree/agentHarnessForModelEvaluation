import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { benchmarkById, benchmarks } from "../data/catalog";
import { Empty, Page } from "../components/ui";

export function BenchmarkList() {
  const [phase, setPhase] = useState<"ready" | "loading" | "error">("ready");
  const [query, setQuery] = useState("");
  const visible = benchmarks.filter((item) => {
    const hay = `${item.name} ${item.summary} ${item.tasks.map((task) => task.name).join(" ")}`.toLowerCase();
    return hay.includes(query.trim().toLowerCase());
  });

  return (
    <Page
      kicker="评测集"
      title="任务与兼容状态"
      lede="兼容状态标成演示或未接入，不表示 Harbor 0.25.0 已经验收了这些评测集的最新功能。"
    >
      <div className="toolbar">
        <label>
          搜索
          <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="名称或任务" />
        </label>
        <button type="button" className="text-button" onClick={() => setQuery("")}>
          清除
        </button>
        <button
          type="button"
          className="text-button"
          onClick={() => {
            setPhase("loading");
            window.setTimeout(() => setPhase("ready"), 700);
          }}
        >
          演示加载
        </button>
        <button type="button" className="text-button" onClick={() => setPhase("error")}>
          演示错误
        </button>
      </div>
      {phase === "loading" ? <p className="notice">正在读取演示目录…</p> : null}
      {phase === "error" ? (
        <div className="empty" role="alert">
          <p>演示目录没有加载成功。这是页面状态，不是评测失败。</p>
          <button type="button" onClick={() => setPhase("ready")}>
            重试
          </button>
        </div>
      ) : null}
      {phase === "ready" && visible.length === 0 ? (
        <Empty title="没有符合筛选的评测集。">
          <button type="button" onClick={() => setQuery("")}>
            清除搜索
          </button>
        </Empty>
      ) : null}
      {phase === "ready" ? (
        <div className="stack">
          {visible.map((item) => (
            <article key={item.id} className="block">
              <div className="title-row">
                <h2>
                  <Link to={`/benchmarks/${item.id}`}>{item.name}</Link>
                </h2>
                <span className="pill" data-state={item.canCreate ? "draft" : "missing"}>
                  {item.canCreate ? "可创建演示" : "未接入"}
                </span>
              </div>
              <p>{item.summary}</p>
              <p className="fine">
                版本 {item.version} · {item.tasks.length} 道题 · {item.compatibility}
              </p>
            </article>
          ))}
        </div>
      ) : null}
    </Page>
  );
}

export function BenchmarkDetail() {
  const { benchmarkId } = useParams();
  const benchmark = benchmarkById(benchmarkId ?? "");
  const [selected, setSelected] = useState<string[]>(benchmark?.canCreate ? benchmark.tasks.map((task) => task.id) : []);
  if (!benchmark) return <Page kicker="评测集" title="没有这个评测集" />;

  function toggle(id: string) {
    setSelected((current) => (current.includes(id) ? current.filter((item) => item !== id) : [...current, id]));
  }

  const createTo = `/experiments/new?benchmark=${benchmark.id}&tasks=${selected.join(",")}`;

  return (
    <Page
      kicker="评测集"
      title={benchmark.name}
      lede={benchmark.summary}
      actions={
        !benchmark.canCreate ? (
          <button type="button" disabled>
            不能创建
          </button>
        ) : selected.length === 0 ? (
          <button type="button" disabled>
            先选择任务
          </button>
        ) : (
          <Link className="button" to={createTo}>
            用所选任务创建实验
          </Link>
        )
      }
    >
      <p className="notice">{benchmark.compatibility}</p>
      <p className="fine">运行要求：{benchmark.requirements}</p>
      {!benchmark.canCreate ? <p className="fine">未实现创建。这个评测集在原型里没有成绩。</p> : null}
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>选择</th>
              <th>任务</th>
              <th>能力标签</th>
              <th>说明</th>
            </tr>
          </thead>
          <tbody>
            {benchmark.tasks.map((task) => (
              <tr key={task.id}>
                <td>
                  <input
                    type="checkbox"
                    aria-label={`选择 ${task.name}`}
                    checked={selected.includes(task.id)}
                    disabled={!benchmark.canCreate}
                    onChange={() => toggle(task.id)}
                  />
                </td>
                <td>{task.name}</td>
                <td>{task.tags.join("、")}</td>
                <td>{task.instruction}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Page>
  );
}
