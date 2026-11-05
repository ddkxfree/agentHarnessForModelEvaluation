import { createContext, useContext, useEffect, useMemo, useState, useRef, type ReactNode } from "react";
import { seedExperiments } from "../data/seed";
import { validateDraft } from "../lib/draft";
import { cancelExperiment, createDraft, startExperiment, tick, updateDraft } from "../lib/simulate";
import type { DraftInput, Experiment } from "../types";

const STORAGE_KEY = "eval-studio-demo-v1";

interface StudioValue {
  experiments: Experiment[];
  create: (input: DraftInput) => { ok: true; experiment: Experiment } | { ok: false; message: string };
  update: (id: string, input: DraftInput) => string | null;
  start: (id: string) => void;
  cancel: (id: string) => void;
  reset: () => void;
}

const StudioContext = createContext<StudioValue | null>(null);

function strip(experiments: Experiment[]): Experiment[] {
  return experiments.map((experiment) => ({
    ...experiment,
    updatedAtMs: null,
    samples: experiment.samples.map((sample) =>
      sample.outcome === "running" ? { ...sample, runningSince: null } : sample,
    ),
  }));
}

function load(): Experiment[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY) ?? sessionStorage.getItem(STORAGE_KEY);
    if (!raw) return seedExperiments();
    const parsed = JSON.parse(raw) as Experiment[];
    if (!Array.isArray(parsed) || parsed.length === 0) return seedExperiments();
    return strip(parsed);
  } catch {
    return seedExperiments();
  }
}

export function StudioProvider({ children }: { children: ReactNode }) {
  const [experiments, setExperiments] = useState<Experiment[]>(load);

  const stored = useRef<string | null>(null);
  useEffect(() => {
    const raw = JSON.stringify(strip(experiments));
    if (raw === stored.current) return;
    try {
      localStorage.setItem(STORAGE_KEY, raw);
      stored.current = raw;
    } catch {
      console.warn("无法保存演示数据；当前页面仍可使用，但关闭后可能丢失。");
    }
  }, [experiments]);

  useEffect(() => {
    const sync = (event: StorageEvent) => {
      if (event.key !== STORAGE_KEY || !event.newValue || event.newValue === stored.current) return;
      try {
        const parsed = JSON.parse(event.newValue) as Experiment[];
        if (!Array.isArray(parsed) || !parsed.every(item => item.id && Array.isArray(item.samples))) return;
        stored.current = event.newValue;
        setExperiments(strip(parsed));
      } catch { /* Ignore malformed storage from another tab. */ }
    };
    window.addEventListener("storage", sync);
    return () => window.removeEventListener("storage", sync);
  }, []);

  useEffect(() => {
    const timer = window.setInterval(() => {
      // Reports and evidence tabs observe; they never drive simulated execution.
      if (document.visibilityState !== "visible" || /\/report$|\/evidence$|\/steps\/|^\/help/.test(window.location.pathname)) return;
      const now = Date.now();
      setExperiments((current) => {
        let changed = false;
        const next = current.map((experiment) => {
          const updated = tick(experiment, now);
          if (updated !== experiment) changed = true;
          return updated;
        });
        return changed ? next : current;
      });
    }, 1000);
    return () => window.clearInterval(timer);
  }, []);

  const api = useMemo<StudioValue>(() => {
    return {
      experiments,
      create(input) {
        const message = validateDraft(input);
        if (message) return { ok: false, message };
        const experiment = createDraft(input, Date.now(), `exp-${crypto.randomUUID().slice(0, 8)}`);
        setExperiments((current) => [experiment, ...current]);
        return { ok: true, experiment };
      },
      update(id, input) {
        const message = validateDraft(input);
        if (message) return message;
        setExperiments((current) =>
          current.map((experiment) =>
            experiment.id === id ? updateDraft(experiment, input, Date.now()) : experiment,
          ),
        );
        return null;
      },
      start(id) {
        const now = Date.now();
        setExperiments((current) =>
          current.map((experiment) => (experiment.id === id ? startExperiment(experiment, now) : experiment)),
        );
      },
      cancel(id) {
        const now = Date.now();
        setExperiments((current) =>
          current.map((experiment) => (experiment.id === id ? cancelExperiment(experiment, now) : experiment)),
        );
      },
      reset() {
        try { sessionStorage.removeItem(STORAGE_KEY); } catch { /* Storage may be unavailable. */ }
        setExperiments(seedExperiments());
      },
    };
  }, [experiments]);

  return <StudioContext.Provider value={api}>{children}</StudioContext.Provider>;
}

export function useStudio(): StudioValue {
  const value = useContext(StudioContext);
  if (!value) throw new Error("StudioProvider missing");
  return value;
}

export function useExperiment(id: string | undefined): Experiment | undefined {
  const { experiments } = useStudio();
  return experiments.find((experiment) => experiment.id === id);
}
