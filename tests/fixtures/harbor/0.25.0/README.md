# Harbor 0.25.0 job fixtures

Real job directories written by `harbor run` 0.25.0 (installed from
`configs/harbor/requirements-0.25.0.lock`) on the pinned Terminal-Bench 2.0 task `fix-git`
with the `oracle` agent. Only local paths were rewritten (`/cache/...`, `/opt/harbor-0.25.0`,
`/work`, `jobs/`); nothing else was edited. Neither run used a model or credentials.

| directory | what happened | what it exercises |
|---|---|---|
| `oracle-env-error` | Docker had no compose plugin: the environment never started | trial exception (`RuntimeError`, phase `environment_setup`), `exception.txt`, job exit code 0 with no valid trial; the trial's `trial_uri` still names the job directory it was first written to |
| `oracle-verifier-offline` | the oracle solution ran, but the verifier could not download its test tools (no network from compose networks on the capture VM) | a completed trial with `reward 0.0`, i.e. a scored failure that is not an exception |

Other cases (multiple rewards, partial jobs, unknown schema, symbolic links, model usage) are
derived from these in `tests/test_harbor.py`.
