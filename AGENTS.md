# Agent guide

Read `docs/roadmap.md` first: it records the project's goal, design decisions and the next stages with acceptance criteria. `docs/progress/` has one write-up per completed round of work.

## Development

```bash
uv sync
uv run pytest -q                                  # unit tests use MockModel, in-memory tasks and a fake sandbox
uv run ruff check . && uv run ruff format --check .
```

`tests/test_docker_sandbox.py` runs against a real Docker daemon and is skipped when Docker is unavailable. On a VM without a service manager, start the daemon with `sudo dockerd` (e.g. in tmux) and make `/var/run/docker.sock` accessible.

For real end-to-end checks without API keys, use Ollama:

```bash
ollama pull qwen2.5:0.5b-instruct && uv run harness run -c configs/ollama-smoke.yaml
ollama pull qwen2.5:3b-instruct && uv run harness run -c configs/terminal-ollama.yaml   # needs Docker
uv run harness run -m none/none -t terminal -p terminal.solver=oracle                  # validates the tasks
uv run python examples/onboard/demo.py /tmp/onboard-demo                              # onboarding workflow, scripted replies
```

The onboarding example in `examples/onboard/world-capitals/` is pinned by `source_sha256` in `configs/onboard-world-capitals.yaml`; update the pin whenever a file there changes.

## Conventions

- New benchmarks go in `src/harness/tasks/<name>.py` and register with `@register("<name>")`. Adding a benchmark must not require changes to `core/`.
- Pin every dataset to a HuggingFace revision SHA; local task sets are pinned by content fingerprint.
- Keep generation and scoring separate where possible: plain scorers only see the sample and the raw model output, so `harness rescore` works offline. Scorers that must inspect the sandbox implement `SandboxScorer`.
- Anything that changes what a model sees or how it is scored must show up in the solver/scorer `describe()` output, so it lands in the run manifest and `harness diff` can detect it.
- Environment failures raise `SandboxError` so they are reported as `env_error`, not as model failures.
- Every terminal-style task must be solved by the oracle solver and failed by an agent that does nothing; `tests/test_docker_sandbox.py` checks both.
- Unit tests must not need network access or API keys.
- After each round of work, add `docs/progress/NN-<topic>.md` (in Chinese, matching the existing ones) and update `docs/progress/README.md` and the status section of `docs/roadmap.md`.
