import argparse
import json
import os

from . import EchoModel, OpenAICompatibleModel, evaluate, load_jsonl


def main():
    p = argparse.ArgumentParser(description="Evaluate an LLM on a JSONL benchmark")
    p.add_argument("dataset")
    p.add_argument("--model", default="echo")
    p.add_argument("--base-url", default="https://api.openai.com/v1")
    a = p.parse_args()
    model = EchoModel() if a.model == "echo" else OpenAICompatibleModel(
        a.model, os.environ.get("OPENAI_API_KEY", ""), a.base_url)
    report = evaluate(model, load_jsonl(a.dataset))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
