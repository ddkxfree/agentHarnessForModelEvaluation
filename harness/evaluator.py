import json
from dataclasses import dataclass


@dataclass
class Sample:
    prompt: str
    answer: str


def load_jsonl(path):
    """Load samples from a JSONL file with 'prompt' and 'answer' fields."""
    with open(path, encoding="utf-8") as f:
        return [Sample(d["prompt"], d["answer"]) for d in map(json.loads, filter(str.strip, f))]


def _norm(s):
    return " ".join(s.lower().split())


def evaluate(model, samples):
    """Run model on samples; score by normalized exact match. Returns summary dict."""
    results = []
    for s in samples:
        try:
            output = model.generate(s.prompt)
        except Exception as e:  # a failing call counts as incorrect
            output = f"<error: {e}>"
        results.append({"prompt": s.prompt, "expected": s.answer, "output": output,
                        "correct": _norm(output) == _norm(s.answer)})
    correct = sum(r["correct"] for r in results)
    return {"model": model.name, "total": len(results), "correct": correct,
            "accuracy": correct / len(results) if results else 0.0, "results": results}
