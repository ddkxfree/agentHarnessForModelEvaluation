# World Capitals (frozen onboarding example)

A tiny question-answering set used to exercise the onboarding workflow. It is not in this
platform's format and is not a benchmark.

## Files

- `data/capitals.tsv`: tab-separated, UTF-8, one header line, then one question per line.
  There is a single split, `test`.

## Fields

| column | meaning |
|---|---|
| `qid` | stable question id, e.g. `q3` |
| `country` | the country asked about |
| `capital` | the gold answer |
| `alt_names` | other accepted spellings or cities, separated by `|`; may be empty |

## Task

Ask: `What is the capital of <country>? Answer with the city name only.`

Each sample's input is that question, exactly as written, and it is the prompt the model is
shown, with no other text. The target is `capital`; `alt_names` are further accepted answers,
not targets. The model must never be shown the answer.

Sample ids must be `world-capitals/<qid>`, so results can be matched across versions.

Scoring follows `SCORING.md`.
