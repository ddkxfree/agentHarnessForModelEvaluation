import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { agents, benchmarkById, benchmarks, models, runtimes } from "../data/catalog";
import { validateDraft } from "../lib/draft";
import { parseDemoInstruction, PARSE_EXAMPLES } from "../lib/nl";
import { useStudio } from "../state/Studio";
import type { DraftInput, Experiment } from "../types";

export function ExperimentForm({
  initial,
  experiment,
}: {
  initial: DraftInput;
  experiment?: Experiment;
}) {
  const navigate = useNavigate();
  const studio = useStudio();
  const [draft, setDraft] = useState<DraftInput>(initial);
  const [nl, setNl] = useState(PARSE_EXAMPLES[0]);
  const [parseNote, setParseNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const benchmark = benchmarkById(draft.benchmarkId);
  const creatable = useMemo(() => benchmarks.filter((item) => item.canCreate), []);
  const locked = experiment != null && experiment.status !== "draft";

  function apply(next: DraftInput) {
    setDraft(next);
    setError(validateDraft(next));
  }

  function onBenchmark(id: string) {
    const next = benchmarkById(id);
    apply({
      ...draft,
      benchmarkId: id,
      taskIds: next?.tasks.map((task) => task.id) ?? [],
      name: draft.name,
    });
  }

  function toggleTask(id: string) {
    const taskIds = draft.taskIds.includes(id)
      ? draft.taskIds.filter((item) => item !== id)
      : [...draft.taskIds, id];
    apply({ ...draft, taskIds });
  }

  function parse() {
    const result = parseDemoInstruction(nl);
    if (!result.ok) {
      setParseNote(null);
      setError(result.message);
      return;
    }
    setParseNote(result.note);
    apply(result.draft);
  }

  function save() {
    const message = validateDraft(draft);
    if (message) {
      setError(message);
      return;
    }
    if (experiment) {
      const updateError = studio.update(experiment.id, draft);
      setError(updateError);
      return;
    }
    const created = studio.create(draft);
    if (!created.ok) {
      setError(created.message);
      return;
    }
    navigate(`/experiments/${created.experiment.id}`);
  }

  return (
    <form
      className="form"
      onSubmit={(event) => {
        event.preventDefault();
        if (!locked) save();
      }}
    >
      {experiment ? null : (
        <fieldset>
          <legend>自然语言配置</legend>
          <label>
            指令
            <textarea value={nl} onChange={(event) => setNl(event.target.value)} rows={3} />
          </label>
          <div className="row-actions">
            <button type="button" onClick={parse}>
              演示解析
            </button>
            {PARSE_EXAMPLES.map((example) => (
              <button key={example} type="button" className="text-button" onClick={() => setNl(example)}>
                填入示例
              </button>
            ))}
          </div>
          {parseNote ? <p className="fine">{parseNote}</p> : null}
        </fieldset>
      )}
      <div className="form-grid">
        <label>
          名称
          <input
            value={draft.name}
            disabled={locked}
            onChange={(event) => apply({ ...draft, name: event.target.value })}
          />
        </label>
        <label>
          评测集
          <select
            value={draft.benchmarkId}
            disabled={locked}
            onChange={(event) => onBenchmark(event.target.value)}
          >
            {creatable.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          版本
          <input value={benchmark?.version ?? "未记录"} readOnly />
        </label>
        <label>
          模型
          <select
            value={draft.modelId}
            disabled={locked}
            onChange={(event) => apply({ ...draft, modelId: event.target.value })}
          >
            {models.map((item) => (
              <option key={item.id} value={item.id}>
                {item.alias ? `${item.label}（${item.alias}）` : item.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          Agent
          <select
            value={draft.agentId}
            disabled={locked}
            onChange={(event) => {
              const agent = agents.find((item) => item.id === event.target.value);
              apply({ ...draft, agentId: event.target.value, runtimeId: agent?.runtimeId ?? draft.runtimeId });
            }}
          >
            {agents.map((item) => (
              <option key={item.id} value={item.id}>
                {item.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          执行环境
          <select
            value={draft.runtimeId}
            disabled={locked}
            onChange={(event) => apply({ ...draft, runtimeId: event.target.value })}
          >
            {runtimes.map((item) => (
              <option key={item.id} value={item.id}>
                {item.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          重复次数
          <input
            type="number"
            min={1}
            max={5}
            value={draft.repeats}
            disabled={locked}
            onChange={(event) => apply({ ...draft, repeats: Number(event.target.value) })}
          />
        </label>
        <label>
          并发
          <input
            type="number"
            min={1}
            max={4}
            value={draft.concurrency}
            disabled={locked}
            onChange={(event) => apply({ ...draft, concurrency: Number(event.target.value) })}
          />
        </label>
        <label>
          token 上限
          <input
            inputMode="numeric"
            placeholder="未设置"
            value={draft.maxTokens ?? ""}
            disabled={locked}
            onChange={(event) =>
              apply({
                ...draft,
                maxTokens: event.target.value === "" ? null : Number(event.target.value),
              })
            }
          />
        </label>
        <label>
          步数上限
          <input
            inputMode="numeric"
            placeholder="未设置"
            value={draft.maxSteps ?? ""}
            disabled={locked}
            onChange={(event) =>
              apply({
                ...draft,
                maxSteps: event.target.value === "" ? null : Number(event.target.value),
              })
            }
          />
        </label>
      </div>
      <fieldset>
        <legend>任务范围</legend>
        <ul className="check-list">
          {(benchmark?.tasks ?? []).map((task) => (
            <li key={task.id}>
              <label>
                <input
                  type="checkbox"
                  checked={draft.taskIds.includes(task.id)}
                  disabled={locked}
                  onChange={() => toggleTask(task.id)}
                />
                <span>{task.name}</span>
                <small>{task.tags.join("、")}</small>
              </label>
            </li>
          ))}
        </ul>
      </fieldset>
      {error ? (
        <p className="form-error" role="alert">
          {error}
        </p>
      ) : null}
      {locked ? <p className="fine">运行开始后配置锁定。</p> : null}
      <div className="row-actions">
        <button type="submit" disabled={locked || error != null}>
          {experiment ? "保存配置" : "创建实验"}
        </button>
      </div>
    </form>
  );
}
