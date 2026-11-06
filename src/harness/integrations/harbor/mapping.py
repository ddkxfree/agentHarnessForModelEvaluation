"""From one Harbor 0.25.0 trial record to the fields this harness shows and compares.

Only what Harbor recorded is mapped. Nothing is invented: a missing token count stays None,
an exception type we do not know stays "unknown", and a result with several rewards (or a
non-binary one) is kept as recorded and left unscored rather than reduced to one number.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

MAPPING_VERSION = 2

# Agents of the pinned release that never call a model: "no usage" is then not applicable,
# which is different from a model agent that did not report its usage.
NO_MODEL_AGENTS = frozenset({"oracle", "nop"})

# Harbor exception class name -> category. Anything not listed is "unknown".
EXCEPTION_CATEGORIES = {
    "AgentTimeoutError": "agent_timeout",
    "VerifierTimeoutError": "verifier_timeout",
    "EnvironmentStartTimeoutError": "environment_timeout",
    "AgentSetupTimeoutError": "agent_setup_timeout",
    "RewardFileNotFoundError": "verifier_error",
    "RewardFileEmptyError": "verifier_error",
    "VerifierOutputParseError": "verifier_error",
    "ApiUsageLimitError": "api_error",
    "ModelNotFoundError": "api_error",
    "AgentAuthenticationError": "api_error",
    "UnsupportedModelParametersError": "api_error",
    "AgentSafetyRefusalError": "agent_refusal",
    "CancelledError": "cancelled",
    "KeyboardInterrupt": "cancelled",
}

PHASES = ("environment_setup", "agent_setup", "agent_execution", "verifier")


def _ts(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _seconds(span: Any) -> float | None:
    if not isinstance(span, dict):
        return None
    a, b = _ts(span.get("started_at")), _ts(span.get("finished_at"))
    if a is None or b is None:
        return None
    if (a.tzinfo is None) != (b.tzinfo is None):
        return None
    return round((b - a).total_seconds(), 3)


def _failed_phase(r: dict[str, Any]) -> str | None:
    """The last phase that started; where an exception most likely happened."""
    last = None
    for phase in PHASES:
        if isinstance(r.get(phase), dict) and r[phase].get("started_at"):
            last = phase
    return last


def score(rewards: Any) -> dict[str, Any]:
    """correct/wrong only for a single binary ``reward``; everything else is kept unscored."""
    if not isinstance(rewards, dict) or not rewards:
        return {"correct": None, "note": "no reward recorded"}
    if set(rewards) != {"reward"}:
        return {"correct": None, "note": f"{len(rewards)} reward keys; not aggregated"}
    value = rewards["reward"]
    if isinstance(value, bool) or not isinstance(value, int | float):
        return {"correct": None, "note": f"non-numeric reward {value!r}"}
    if value not in (0, 1):
        return {"correct": None, "note": f"partial reward {value}; not aggregated"}
    return {"correct": value == 1, "note": None}


def usage(r: dict[str, Any], agent: str | None) -> dict[str, Any]:
    ar = r.get("agent_result") if isinstance(r.get("agent_result"), dict) else {}
    values = {
        "input_tokens": ar.get("n_input_tokens"),
        "output_tokens": ar.get("n_output_tokens"),
        "cache_tokens": ar.get("n_cache_tokens"),
        "cost_usd": ar.get("cost_usd"),
    }
    model_info = (r.get("agent_info") or {}).get("model_info")
    if agent in NO_MODEL_AGENTS and model_info is None:
        return {
            **values,
            "status": "not_applicable",
            "source": f"{agent} agent makes no model calls",
        }
    reported = any(v is not None for v in values.values())
    return {
        **values,
        "status": "reported" if reported else "unknown",
        "source": "harbor agent_result" if reported else "agent did not report usage",
    }


def trial_status(r: dict[str, Any]) -> str:
    if r.get("exception_info"):
        exc = r["exception_info"].get("exception_type")
        return "cancelled" if EXCEPTION_CATEGORIES.get(exc) == "cancelled" else "error"
    if not r.get("finished_at"):
        return "running"
    if isinstance(r.get("verifier_result"), dict):
        return "completed"
    return "invalid"


def map_trial(r: dict[str, Any], trial_lock: dict[str, Any] | None) -> dict[str, Any]:
    """The shared view of one trial. ``r`` is the trial's result.json."""
    agent_info = r.get("agent_info") if isinstance(r.get("agent_info"), dict) else {}
    agent = agent_info.get("name")
    model_info = agent_info.get("model_info")
    status = trial_status(r)
    rewards = (r.get("verifier_result") or {}).get("rewards") if status == "completed" else None
    s = score(rewards) if status == "completed" else {"correct": None, "note": f"trial {status}"}
    if status != "completed":
        outcome = status
    elif s["correct"] is None:
        outcome = "unscored"
    else:
        outcome = "correct" if s["correct"] else "wrong"
    exc = r.get("exception_info") if isinstance(r.get("exception_info"), dict) else None
    exception = None
    if exc:
        etype = exc.get("exception_type") or "unknown"
        exception = {
            "type": etype,
            "category": EXCEPTION_CATEGORIES.get(etype, "unknown"),
            "phase": _failed_phase(r),
            "message": (exc.get("exception_message") or "")[:2000],
            "occurred_at": exc.get("occurred_at"),
        }
    task_lock = (trial_lock or {}).get("task") or {}
    config = r.get("config") if isinstance(r.get("config"), dict) else {}
    return {
        "trial_name": r.get("trial_name"),
        "trial_id": r.get("id"),
        "job_id": config.get("job_id"),
        "task_name": r.get("task_name"),
        "task_checksum": r.get("task_checksum"),
        "task_digest": task_lock.get("digest"),
        "agent": {
            "name": agent,
            "version": agent_info.get("version"),
            "model": model_info,
            "kwargs": (config.get("agent") or {}).get("kwargs"),
        },
        # set for Harbor regrades; retried attempts are removed by Harbor and only counted
        # per job (stats.n_retries)
        "regrade_of": config.get("source_trial"),
        "status": status,
        "generation_status": "failed"
        if exception and exception.get("phase") == "agent_execution"
        else "completed"
        if (r.get("agent_execution") or {}).get("finished_at")
        else "unknown",
        "generation_finish_reason": None,
        "assessment_status": "completed"
        if rewards is not None
        else "failed"
        if exception and exception.get("phase") == "verifier"
        else "unknown",
        "phase_evidence": {p: r.get(p) for p in PHASES},
        "outcome": outcome,
        "rewards": rewards,
        "score_note": s["note"],
        "exception": exception,
        "usage": usage(r, agent),
        "timing_s": {
            "total": _seconds(r),
            **{p: _seconds(r.get(p)) for p in PHASES},
        },
        "started_at": r.get("started_at"),
        "finished_at": r.get("finished_at"),
    }


def number_repeats(trials: list[dict[str, Any]]) -> None:
    """Independent repeats of the same task, task content and agent are numbered by start time."""
    groups: dict[tuple, list[dict[str, Any]]] = {}
    for t in trials:
        key = (t["task_name"], t["task_checksum"], t["agent"]["name"], str(t["agent"]["model"]))
        groups.setdefault(key, []).append(t)
    for group in groups.values():
        for i, t in enumerate(
            sorted(group, key=lambda t: (t["started_at"] or "", t["trial_name"]))
        ):
            t["repeat"] = i
