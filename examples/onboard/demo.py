"""End-to-end onboarding run with scripted replies instead of a real model.

    uv run python examples/onboard/demo.py [OUTPUT_DIR]

The scripted "agent" plans, writes an adapter that reads a wrong column, receives the
validator's diagnosis and writes the fixed adapter. Everything else is the real workflow:
spec pinning, the candidate workspace, independent validation, archive and diff. It shows the
plumbing, not what a real model can do. The scripted candidate is trusted, so it is validated
in a host subprocess; real model candidates are validated in a container (the default).
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from harness.core.model import MockModel
from harness.onboard.agent import OnboardAgent
from harness.onboard.spec import load_spec

ROOT = Path(__file__).resolve().parents[2]
GOOD = (ROOT / "tests" / "onboard_fixtures" / "good_adapter.py").read_text()
PLAN = {
    "field_mapping": {
        "id": "world-capitals/<qid>",
        "input": "question template with country",
        "target": "capital",
        "metadata": "alt_names split on |",
    },
    "plan": "Read the TSV, ask per country, normalized exact match against capital or alt names.",
    "questions": [],
}
WRONG_COLUMN = GOOD.replace("r['qid']", "r['id']")
REPLIES = [
    json.dumps(PLAN),
    f'<file path="adapter.py">\n{WRONG_COLUMN}</file>',
    f'<file path="adapter.py">\n{GOOD}</file>',
]


def main() -> None:
    spec = load_spec(ROOT / "configs" / "onboard-world-capitals.yaml")
    spec = spec.model_copy(
        update={
            "source": ROOT / spec.source,
            "executor": "subprocess",
            "output_dir": Path(sys.argv[1]) if len(sys.argv) > 1 else Path("runs/onboard-demo"),
        }
    )
    calls: list[int] = []

    def scripted(messages):
        calls.append(1)
        return REPLIES[len(calls) - 1]

    attempt = asyncio.run(OnboardAgent(spec, MockModel(scripted, name="scripted-demo")).run())
    record = json.loads((attempt / "attempt.json").read_text())
    print(f"{record['status']} ({record['stop_reason']}) in {attempt}")
    for rnd in record["rounds"]:
        print(f"  round {rnd['round']}: {rnd['category'] or 'passed'}")
        if rnd.get("validation", {}).get("detail"):
            print("    " + rnd["validation"]["detail"].splitlines()[0])
    print(f"  diff: {attempt / record['diff']}")


if __name__ == "__main__":
    main()
