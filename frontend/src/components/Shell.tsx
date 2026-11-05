import { Link, NavLink, Outlet } from "react-router-dom";

const NAV = [
  { to: "/", label: "工作台", end: true },
  { to: "/benchmarks", label: "评测集", end: false },
  { to: "/experiments", label: "实验中心", end: false },
  { to: "/traces", label: "轨迹分析", end: false },
  { to: "/compare", label: "实验对比", end: false },
  { to: "/connections", label: "模型与连接", end: false },
];

export function Shell() {
  return (
    <div className="shell">
      <a className="skip" href="#content">
        跳到内容
      </a>
      <aside className="sidenav">
        <p className="brand">
          <span>Eval Studio</span>
          <small>智能体评测工作台</small>
        </p>
        <nav aria-label="一级模块">
          {NAV.map((item) => (
            <NavLink key={item.to} to={item.to} end={item.end}>
              {item.label}
            </NavLink>
          ))}
        </nav>
        <div className="side-note"><Link to="/help/metrics">指标与使用说明 ↗</Link><p>演示空间 · 本地保存<br />不调用模型，不产生费用。</p></div>
      </aside>
      <main id="content">
        <Outlet />
      </main>
    </div>
  );
}
