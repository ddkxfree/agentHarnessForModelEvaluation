"""Read-only progress and standalone HTML from Harbor's persisted evidence."""

from __future__ import annotations

import html
import json
import os
import time
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

from harness.integrations.harbor import importer


def _read(path: Path, limit: int = 2_000_000, *, tail: bool = False) -> str:
    if path.is_symlink() or not path.is_file():
        return ""
    with path.open("rb") as stream:
        size = os.fstat(stream.fileno()).st_size
        if tail:
            stream.seek(max(0, size - limit))
        data = stream.read(limit)
    text = data.decode("utf-8", errors="replace")
    if size > limit and not tail:
        text += "\n[显示已截断；完整内容保存在原始快照中]"
    return text


def _json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(_read(path))
        return value if isinstance(value, dict) else {}
    except (ValueError, OSError):
        return {}


def _inside(root: Path, relative: str) -> Path:
    path = root / relative
    if Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise ValueError("Invalid evidence path")
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("Evidence path escapes snapshot")
    if any(p.is_symlink() for p in [path, *path.parents] if p != root.parent):
        raise ValueError("Symlink evidence is not supported")
    return path


def progress_snapshot(job_dir: Path, *, now: float | None = None) -> dict[str, Any]:
    """Infer stages from existing evidence; no model or Docker calls, bounded log reads."""
    now = time.time() if now is None else now
    trials = []
    for directory in sorted(job_dir.iterdir()) if job_dir.is_dir() else []:
        if directory.is_symlink() or not directory.is_dir():
            continue
        if not (directory / "config.json").is_file():
            continue
        stage = "准备环境"
        activity = "等待阶段日志"
        result = _json(directory / "result.json")
        log = _read(directory / "trial.log", 16_384, tail=True)
        if "Running command:" in log:
            stage = "安装 Agent"
        if "--output-format=stream-json" in log:
            stage = "Agent 作答"
        stream = directory / "agent/cursor-cli.txt"
        if stream.is_file() and not stream.is_symlink():
            stage = "Agent 作答"
            for line in _read(stream, 32_768, tail=True).splitlines():
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(event, dict):
                    continue
                kind = event.get("type")
                if kind == "tool_call":
                    tools = event.get("tool_call") or {}
                    activity = "工具 " + ", ".join(tools) if isinstance(tools, dict) else "工具活动"
                    activity += " / " + str(event.get("subtype", ""))
                    if isinstance(tools, dict):
                        for tool in tools.values():
                            args = tool.get("args") if isinstance(tool, dict) else None
                            if isinstance(args, dict):
                                command = args.get("command") or args.get("path")
                                if isinstance(command, str):
                                    activity += " · " + " ".join(command.split())[:160]
                                    break
                elif kind == "assistant":
                    activity = "收到 Agent 回复"
                elif kind == "thinking":
                    activity = "收到模型活动事件"
                elif kind == "result":
                    stage, activity = "等待评分／收尾", "Agent 已返回结束事件"
        verifier = directory / "verifier"
        if verifier.is_dir() and any(p.is_file() for p in verifier.iterdir()):
            stage, activity = "评分／收尾", "评分文件已写入"
        if "Collecting main service artifacts" in log:
            stage, activity = "收集产物／收尾", "正在收集运行产物"
        if result:
            stage = "已结束"
            activity = "异常结束" if result.get("exception_info") else "评分结果已保存"
        paths = [directory / "trial.log", stream, directory / "result.json"]
        if verifier.is_dir() and not verifier.is_symlink():
            paths.extend(verifier.iterdir())
        updated = max(
            (p.stat().st_mtime for p in paths if p.is_file() and not p.is_symlink()),
            default=directory.stat().st_mtime,
        )
        trials.append(
            {
                "name": directory.name,
                "stage": stage,
                "activity": activity,
                "quiet_s": max(0, int(now - updated)),
            }
        )
    return {"trials": trials, "stages": dict(Counter(t["stage"] for t in trials))}


def progress_printer(run_dir: Path, emit: Callable[[str], None]) -> Callable[[dict], None]:
    receipt = _json(run_dir / "run.json")
    job_dir = _inside(run_dir, receipt["job_dir"])
    last = -10.0
    output = emit
    secrets = importer.all_secrets()

    def emit(message: str) -> None:
        output(importer.redact_text(message, secrets)[0])

    def tick(info: dict) -> None:
        nonlocal last
        elapsed = info.get("elapsed_s", 0)
        phase = info.get("phase")
        if not phase and elapsed - last < 10:
            return
        last = elapsed
        try:
            snapshot = progress_snapshot(job_dir)
            counts = "、".join(f"{k} {v}" for k, v in snapshot["stages"].items())
            emit(f"[进度 {elapsed:.0f}s] {phase or counts or '启动／准备环境'}")
            visible = sorted(snapshot["trials"], key=lambda trial: trial["stage"] == "已结束")
            for trial in visible[:8]:
                emit(
                    f"  {trial['name']} · {trial['stage']} · {trial['activity']} · "
                    f"{trial['quiet_s']}s 未见新日志"
                )
            if len(snapshot["trials"]) > 8:
                emit(f"  其余 {len(snapshot['trials']) - 8} 个任务见阶段汇总")
        except (OSError, ValueError, TypeError):
            emit(f"[进度 {elapsed:.0f}s] 等待可读取的阶段记录")

    return tick


def export_report(run_dir: Path) -> Path:
    """Self-contained inert HTML, using only the redacted snapshot, never local/ originals."""
    run_dir = Path(run_dir)
    record = _json(run_dir / importer.RUN_RECORD)
    if not record:
        raise ValueError("No imported Harbor record; import/rebuild the job first")
    if importer.redaction_version(record) < importer.REDACTION_VERSION:
        raise ValueError("Snapshot redaction is outdated; run harness harbor rebuild first")
    raw = _inside(run_dir, "raw")
    secrets = importer.all_secrets()

    def escape(value: Any) -> str:
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)
        return html.escape(importer.redact_text(text, secrets)[0])

    def pre(value: Any) -> str:
        return "<pre>" + escape(value) + "</pre>"

    def details(title: str, body: str, *, opened: bool = False) -> str:
        tag = "<details open>" if opened else "<details>"
        return tag + f"<summary>{escape(title)}</summary>{body}</details>"

    def table(items: dict) -> str:
        rows = "".join(
            f"<tr><th>{escape(k)}</th><td>{escape('未知' if v is None else v)}</td></tr>"
            for k, v in items.items()
        )
        return "<table>" + rows + "</table>"

    trials_path = run_dir / "trials.jsonl"
    trials = [json.loads(line) for line in _read(trials_path, 50_000_000).splitlines() if line]
    parts = [
        "<h1>评测运行报告</h1>",
        pre(record.get("run_id", run_dir.name)),
        "<p>离线查看：不启动 Harbor、不调用模型。缺失用量为未知；费用不等于账户账单。"
        "逐步 token 与内部 API 重试可能未公开。输入中的缓存包含关系以来源为准。</p>",
        details("运行概况", pre(record.get("job", {})), opened=True),
    ]
    for warning in importer.record_warnings(record):
        parts.append("<p class='warning'>" + escape(warning) + "</p>")
    for trial in trials:
        name = trial["trial_name"]
        parts.append("<section><h2>" + escape(trial.get("task_name") or name) + "</h2>")
        summary = {
            k: trial.get(k)
            for k in (
                "trial_name",
                "agent",
                "outcome",
                "rewards",
                "generation_status",
                "assessment_status",
                "usage",
                "timing_s",
                "exception",
            )
        }
        usage = trial.get("usage") or {}
        timing = trial.get("timing_s") or {}
        parts.append(
            table(
                {
                    "结果": trial.get("outcome"),
                    "分数": trial.get("rewards"),
                    "总耗时（秒）": timing.get("total"),
                    "输入 token（Harbor 口径）": usage.get("input_tokens"),
                    "缓存 token（勿重复加总）": usage.get("cache_tokens"),
                    "输出 token": usage.get("output_tokens"),
                    "费用 USD": usage.get("cost_usd"),
                }
            )
        )
        parts.append(details("完整指标与阶段耗时", pre(summary)))
        trajectory = trial.get("trajectory")
        if trajectory:
            data = _json(_inside(raw, f"{name}/{trajectory}"))
            steps = data.get("steps") or []
            parts.append(f"<h3>执行轨迹 · {len(steps)} 步</h3>")
            if not steps:
                parts.append("<p>轨迹缺失、过大或无法解析，请查看下方原始文件。</p>")
            for step in steps:
                body = pre(step.get("message", ""))
                for label, key in [
                    ("模型公开的推理内容", "reasoning_content"),
                    ("工具调用", "tool_calls"),
                    ("工具返回", "observation"),
                    ("步骤指标（若有）", "metrics"),
                ]:
                    if step.get(key) is not None:
                        body += details(label, pre(step[key]), opened=key == "tool_calls")
                parts.append(
                    details(
                        f"步骤 {step.get('step_id', '?')} · {step.get('source', 'unknown')} · "
                        f"{step.get('timestamp') or '时间未知'}",
                        body,
                        opened=True,
                    )
                )
        else:
            parts.append("<p>未提供结构化轨迹；下方保留现有日志。</p>")
        parts.append("<h3>保存的日志与评分证据</h3>")
        for relative in trial.get("files", []):
            path = _inside(raw, f"{name}/{relative}")
            if path.suffix.lower() in {".txt", ".log", ".json", ".jsonl", ".md", ".yaml"}:
                parts.append(details(relative, pre(_read(path))))
            else:
                parts.append("<p>已归档文件：" + escape(relative) + "</p>")
        parts.append("</section>")
    parts.append(details("运行日志", pre(_read(raw / "job.log"))))
    document = (
        """<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
<title>离线评测报告</title><style>
body{font:16px/1.65 system-ui,sans-serif;background:#f4f6f9;color:#182230;
max-width:1100px;margin:32px auto;padding:0 24px}
section,details{background:white;border:1px solid #d9e0e9;
border-radius:8px;padding:14px;margin:12px 0}
section{padding:24px}summary{cursor:pointer;font-weight:600}
pre{white-space:pre-wrap;overflow-wrap:anywhere;font:13px/1.6 ui-monospace,monospace;
max-height:650px;overflow:auto}
table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:8px;
border-bottom:1px solid #e5e9ef;overflow-wrap:anywhere}
.warning{color:#984b00}h1,h2{line-height:1.3}</style><body>"""
        + "".join(parts)
        + "</body></html>"
    )
    output = run_dir / "report.html"
    temp = run_dir / ".report.html.tmp"
    temp.write_text(document, encoding="utf-8")
    os.replace(temp, output)
    return output
