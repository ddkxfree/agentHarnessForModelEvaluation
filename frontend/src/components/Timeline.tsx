import { Link } from "react-router-dom";
import { useState } from "react";
import type { Step } from "../types";
import { visibleText } from "../lib/text";
import { Dialog, Highlight } from "./ui";

export function Timeline({
  steps,
  query,
  expanded,
  recordBase,
}: {
  steps: Step[];
  query: string;
  expanded: boolean;
  recordBase?: string;
}) {
  const [full, setFull] = useState<Step | null>(null);
  const [copied, setCopied] = useState<string | null>(null);

  async function copy(command: string) {
    try {
      await navigator.clipboard.writeText(command);
    } catch {
      const area = document.createElement("textarea");
      area.value = command;
      document.body.appendChild(area);
      area.select();
      document.execCommand("copy");
      area.remove();
    }
    setCopied(command);
  }

  if (steps.length === 0) {
    return <p className="empty">还没有记录。进行中的样本会在模拟结束时写入轨迹。</p>;
  }

  return (
    <ol className="timeline">
      {steps.map((item, index) => {
        const text = visibleText(item.text);
        const long = text.length > 320;
        return (
          <li
            key={item.id}
            id={`step-${item.id}`}
            data-failure-cause={item.failureCause ? "true" : "false"}
            data-error={item.error ? "true" : "false"}
          >
            <div className="step-index">{index + 1}</div>
            <div className="step-body">
              <div className="step-meta">
                <span>{actorLabel(item)}</span>
                {item.tool ? <strong>{item.tool}</strong> : null}
                {item.failureCause ? <span className="pill" data-state="budget_exhausted">失败原因</span> : null}
                {item.error && !item.failureCause ? <span className="pill" data-state="wrong">错误</span> : null}
                <span className="muted">{item.durationS == null ? "耗时未记录" : `${item.durationS.toFixed(1)} 秒`}</span>
                <span className="muted">逐步 token 未记录</span>
              </div>
              {item.command ? (
                <div className="command">
                  <code>{item.command}</code>
                  <button type="button" onClick={() => copy(item.command ?? "")}>
                    {copied === item.command ? "已复制" : "复制命令"}
                  </button>
                </div>
              ) : null}
              <pre className={expanded || !long ? "log" : "log clipped"}>
                <Highlight text={text} query={query} />
              </pre>
              {recordBase ? <div className="record-links"><Link to={`${recordBase}/steps/${encodeURIComponent(item.id)}`}>完整记录 →</Link><Link target="_blank" rel="noopener noreferrer" to={`${recordBase}/steps/${encodeURIComponent(item.id)}`}>新标签页 ↗</Link></div> : null}
              {long ? (
                <button type="button" className="text-button" onClick={() => setFull(item)}>
                  全屏查看
                </button>
              ) : null}
            </div>
          </li>
        );
      })}
      {full ? <Dialog title="完整记录" text={visibleText(full.text)} onClose={() => setFull(null)} /> : null}
    </ol>
  );
}

function actorLabel(step: Step): string {
  if (step.kind === "tool_call") return "工具调用";
  if (step.kind === "tool_result") return "工具返回";
  if (step.actor === "model") return "模型消息";
  if (step.actor === "user") return "任务说明";
  return "环境记录";
}
