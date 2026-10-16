# agentHarnessForModelEvaluation
这是一个接入主流模型评测的agent，进行大模型评测的平台

## Usage
```
python -m harness data.jsonl --model gpt-4o --base-url https://api.openai.com/v1   # needs OPENAI_API_KEY
python -m pytest tests
```
Dataset: JSONL lines like `{"prompt": "...", "answer": "..."}`. Add models by subclassing `harness.Model`.
