"""Token budgets, solver protocols and how comparisons classify what they vary."""

import asyncio
import json
from pathlib import Path

import pytest
from conftest import make_samples, question_number
from fakes import FakeProvider, call, scripted, tool_turn
from test_agent import agent_task, run

from harness.analysis.compare import CompareSpec, build_report, experiment_type, run_comparison
from harness.cli import _print_comparison
from harness.core.cache import DiskCache
from harness.core.model import MockModel
from harness.core.runner import run_eval, run_samples
from harness.core.scorer import NumberMatchScorer
from harness.core.solver import PromptSolver
from harness.core.task import DatasetSpec, Task, get_task
from harness.core.types import GenerateConfig, ModelOutput, Outcome, Usage
from harness.solvers.basic_agent import BasicAgent

CONFIGS = Path(__file__).resolve().parents[1] / "configs"


def costly(*calls, tokens=100):
    out = tool_turn(*calls)
    return out.model_copy(update={"usage": Usage(input_tokens=tokens - 10, output_tokens=10)})


LS = call("bash", '{"command": "ls"}')


@pytest.mark.parametrize("passes", [False, True])
def test_token_limit_stops_the_agent_and_still_scores(passes):
    task = agent_task(BasicAgent(max_steps=20))
    task.token_limit = 250
    provider = FakeProvider()
    provider.test_exit_code = 0 if passes else 1
    model = scripted(costly(LS))
    (r,), _ = run(task, model, provider)

    # 0, 100 and 200 tokens spent are under the budget; the fourth call is refused.
    assert model.calls == 3 and len(r.steps) == 3
    assert r.error is None and r.output.finish_reason == "token_limit"
    assert r.outcome == (Outcome.CORRECT if passes else Outcome.TOKEN_LIMIT)
    assert r.output.usage.total == 300
    assert provider.created[0].commands[-1] == "bash /tests/test.sh"


def test_without_a_token_limit_the_agent_runs_to_its_step_limit():
    provider = FakeProvider()
    provider.test_exit_code = 1
    model = scripted(costly(LS, tokens=10_000))
    (r,), _ = run(agent_task(BasicAgent(max_steps=4)), model, provider)
    assert model.calls == 4 and r.outcome == Outcome.STEP_LIMIT


def test_replayed_calls_count_towards_the_token_limit(tmp_path):
    cache = DiskCache(tmp_path)
    turns = [costly(LS), costly(LS), costly(LS), costly(LS), costly(LS)]
    run(agent_task(BasicAgent(max_steps=5)), scripted(*turns), cache=cache)

    task = agent_task(BasicAgent(max_steps=5))
    task.token_limit = 250
    second = scripted(*turns)
    (r,), _ = run(task, second, cache=cache)
    assert second.calls == 0
    assert r.calls.cached == 3 and r.calls.fresh == 0
    assert r.output.finish_reason == "token_limit"


def test_token_limit_stops_mini_swe_agent():
    task = get_task("terminal", solver="mini", ids=["hello-file"], max_steps=20, token_limit=250)
    provider = FakeProvider()
    provider.test_exit_code = 1
    model = scripted(costly(LS))
    (r,) = asyncio.run(
        run_samples(task, task.load(), model, GenerateConfig(), sandbox_provider=provider)
    )
    assert model.calls == 3 and len(r.steps) == 3
    assert r.error is None and r.output.finish_reason == "token_limit"
    assert r.outcome == Outcome.TOKEN_LIMIT
    # mini's own messages up to the stop are kept
    assert sum(1 for m in r.messages if m["role"] == "assistant") == 3


def test_token_limit_and_solver_protocol_are_in_the_manifest(tmp_path):
    task = agent_task()
    task.token_limit = 5000
    run_dir = asyncio.run(
        run_eval(
            scripted(tool_turn(call("submit", "{}"))),
            [(task, GenerateConfig())],
            output_dir=tmp_path,
            sandbox_provider=FakeProvider(),
        )
    )
    manifest = json.loads((run_dir / "manifest.json").read_text())
    entry = manifest["tasks"][0]
    assert entry["token_limit"] == 5000
    assert entry["solver_protocol"]["interface"] == "native_tool_calls"
    assert entry["solver_protocol"]["tools"] == ["bash", "submit"]
    assert manifest["results"]["agent_toy"]["outcomes"]["token_limit"] == 0


def test_every_builtin_solver_states_its_protocol():
    for solver in ("agent", "mini", "oracle"):
        protocol = get_task("terminal", solver=solver).solver.protocol()
        assert {"interface", "tools", "finish", "step", "requires"} <= set(protocol)
    mini = get_task("terminal", solver="mini").solver.protocol()
    assert "format error" in mini["format_errors"]
    assert get_task("terminal", solver="oracle").solver.protocol()["step"] == "no model calls"
    agent = BasicAgent(max_observation_chars=500)
    assert "500" in agent.protocol()["observation"]
    # the protocol is descriptive: it does not change the solver's fingerprint
    assert "protocol" not in json.dumps(agent.describe())


def test_task_factories_pass_the_budget_and_observation_options():
    task = get_task("terminal", token_limit=1234, max_observation_chars=500)
    assert task.token_limit == 1234 and task.solver.max_observation_chars == 500
    assert task.params["token_limit"] == 1234
    assert get_task("terminal").solver.max_observation_chars == 4000
    bench = get_task("terminal-bench", token_limit=99, max_observation_chars=700)
    assert bench.token_limit == 99 and bench.solver.max_observation_chars == 700
    with pytest.raises(ValueError, match="only applies to solver 'agent'"):
        get_task("terminal", solver="mini", max_observation_chars=500)
    with pytest.raises(ValueError, match="positive"):
        get_task("terminal", max_observation_chars=0)


def toy_factory(name, prompt="plain", token_limit=None, max_steps=1, limit=4):
    template = {"plain": "{input}", "careful": "Be careful. {input}"}[prompt]
    samples = make_samples()[:limit]
    return Task(
        name=name,
        dataset=DatasetSpec(path="local/toy", split="test"),
        load=lambda: samples,
        solver=PromptSolver(lambda s: template.format(input=s.input), template_text=template),
        scorer=NumberMatchScorer(),
        params={"prompt": prompt, "token_limit": token_limit, "max_steps": max_steps},
        token_limit=token_limit,
    )


def respond(messages):
    n = question_number([{"content": messages[-1]["content"].removeprefix("Be careful. ")}])
    return ModelOutput(text=f"The answer is {2 * n}", usage=Usage(input_tokens=5, output_tokens=2))


def compare(tmp_path, conditions, **params):
    spec = CompareSpec(
        kind="ablation",
        model="mock/mock",
        task="toy",
        params=params,
        conditions=conditions,
        repeats=1,
        cache_dir=tmp_path / "cache",
        output_dir=tmp_path / "runs",
    )
    compare_dir = asyncio.run(run_comparison(spec, MockModel(respond), make_task=toy_factory))
    return build_report(compare_dir)


def test_ablation_of_a_budget_is_reported_as_budget_sensitivity(tmp_path):
    report = compare(
        tmp_path,
        [{"label": "big"}, {"label": "small", "params": {"token_limit": 100}}],
        token_limit=1000,
    )
    assert report["experiment_type"] == "budget_sensitivity"
    assert not report["equal_budget"]
    assert report["conditions"]["big"]["experiment_type"] is None
    assert report["conditions"]["small"]["budgets"]["token_limit"] == 100
    assert report["conditions"]["big"]["budgets"] == {
        "max_steps": 1,
        "command_timeout": None,
        "time_limit": None,
        "token_limit": 1000,
    }
    _print_comparison(report)


def test_ablation_of_a_mechanism_keeps_budgets_equal(tmp_path):
    report = compare(
        tmp_path,
        [{"label": "plain"}, {"label": "careful", "params": {"prompt": "careful"}}],
        token_limit=1000,
    )
    assert report["experiment_type"] == "mechanism_ablation"
    assert report["equal_budget"]
    assert "attributed to that mechanism" in report["experiment_note"]
    _print_comparison(report)


def test_mixed_ablations_are_flagged(tmp_path):
    report = compare(
        tmp_path,
        [
            {"label": "base"},
            {"label": "careful", "params": {"prompt": "careful"}},
            {"label": "steps", "params": {"max_steps": 2}},
        ],
    )
    assert report["experiment_type"] == "mixed"
    assert report["conditions"]["steps"]["experiment_type"] == "budget_sensitivity"


@pytest.mark.parametrize(
    "name, expected",
    [
        ("ablation-observation-terminal-bench.yaml", "mechanism_ablation"),
        ("budget-tokens-terminal.yaml", "budget_sensitivity"),
        ("compare-terminal-ollama.yaml", "solver_comparison"),
    ],
)
def test_shipped_configs_are_valid_and_classified(name, expected):
    spec = CompareSpec.from_yaml(CONFIGS / name)
    assert {experiment_type(spec, c) for c in spec.conditions[1:]} == {expected}
    for condition in spec.conditions:
        task = get_task(spec.task, **spec.params_for(condition))
        assert task.solver.protocol()
