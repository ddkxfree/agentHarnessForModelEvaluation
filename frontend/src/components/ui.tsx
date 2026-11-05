import { useEffect, useState, type ReactNode } from "react";
import { OUTCOME_LABEL, STATUS_LABEL } from "../lib/format";
import { splitHighlight } from "../lib/text";
import type { Outcome } from "../types";

export function Page({
  kicker,
  title,
  lede,
  actions,
  children,
}: {
  kicker: string;
  title: string;
  lede?: ReactNode;
  actions?: ReactNode;
  children?: ReactNode;
}) {
  useEffect(() => {
    document.title = `${title} · Agent Eval Studio`;
  }, [title]);
  return (
    <div className="page">
      <header className="page-head">
        <p className="kicker">{kicker}</p>
        <div className="title-row">
          <h1>{title}</h1>
          {actions ? <div className="title-actions">{actions}</div> : null}
        </div>
        {lede ? <div className="lede">{lede}</div> : null}
      </header>
      {children}
    </div>
  );
}

export function StatusPill({ value }: { value: keyof typeof STATUS_LABEL | Outcome }) {
  const label = value in STATUS_LABEL ? STATUS_LABEL[value as keyof typeof STATUS_LABEL] : OUTCOME_LABEL[value as Outcome];
  return (
    <span className="pill" data-state={value}>
      {label}
    </span>
  );
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="empty">
      <p>{title}</p>
      {children}
    </div>
  );
}

export function Highlight({ text, query }: { text: string; query: string }) {
  const parts = splitHighlight(text, query);
  return (
    <>
      {parts.map((part, index) =>
        part.hit ? <mark key={index}>{part.text}</mark> : <span key={index}>{part.text}</span>,
      )}
    </>
  );
}

export function Metric({ label, value, note }: { label: string; value: string; note: string }) {
  return (
    <div className="metric">
      <p className="metric-label">{label}</p>
      <p className="metric-value">{value}</p>
      <p className="metric-note">{note}</p>
    </div>
  );
}

export function Notice({ children }: { children: ReactNode }) {
  return <p className="notice">{children}</p>;
}

export function useNarrow(max = 960): boolean {
  const query = `(max-width: ${max}px)`;
  const [narrow, setNarrow] = useState(() => window.matchMedia(query).matches);
  useEffect(() => {
    const media = window.matchMedia(query);
    const onChange = () => setNarrow(media.matches);
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, [query]);
  return narrow;
}

export function Dialog({
  title,
  text,
  onClose,
}: {
  title: string;
  text: string;
  onClose: () => void;
}) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="dialog-back" role="presentation" onMouseDown={onClose}>
      <div
        className="dialog"
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className="title-row">
          <h2>{title}</h2>
          <button type="button" onClick={onClose}>
            关闭
          </button>
        </div>
        <pre className="log">{text}</pre>
      </div>
    </div>
  );
}
