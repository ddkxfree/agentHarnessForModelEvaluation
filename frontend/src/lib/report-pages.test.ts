import { createElement as h } from "react";
import type { ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { StudioProvider } from "../state/Studio";
import { Workbench } from "../pages/Workbench";
import { Compare } from "../pages/Compare";
import { ExperimentDetail } from "../pages/ExperimentDetail";
import { TraceRecord } from "../pages/Reference";
import { findSeed } from "../data/seed";

function page(node: ReactNode, url = "/", path = "*") {
  return renderToStaticMarkup(h(MemoryRouter, { initialEntries: [url] },
    h(StudioProvider, null, h(Routes, null, h(Route, { path, element: node })))));
}
afterEach(() => vi.unstubAllGlobals());

describe("overview and standalone reports", () => {
  it("keeps the workbench concise and links to full reports in either tab", () => {
    const html = page(h(Workbench));
    expect(html).toContain('/experiments/exp-ds-basic-30k/report');
    expect(html).toContain('target="_blank"');
    expect(html).not.toContain('按评测集</h2>');
  });
  it("retains all configuration and capability details in the report only", () => {
    const url = '/compare?a=exp-ds-basic-30k&b=exp-qwen-basic-30k';
    const overview = page(h(Compare), url);
    const report = page(h(Compare, { report: true }), url.replace('/compare?', '/compare/report?'));
    expect(overview).toContain('查看完整对比报告');
    expect(overview).not.toContain('<h2>能力标签</h2>');
    expect(report).toContain('<h2>能力标签</h2>');
    expect(report).toContain('id="configuration"');
    expect(report).toContain('exp-qwen-basic-30k');
  });
  it("opens a saved custom experiment in a separate report without an editing form", () => {
    const custom = { ...findSeed('exp-ds-basic-30k'), id: 'custom-saved', name: '持久化自建实验' };
    vi.stubGlobal('localStorage', { getItem: () => JSON.stringify([custom]) });
    const html = page(h(ExperimentDetail, { report: true }), '/experiments/custom-saved/report', '/experiments/:experimentId/report');
    expect(html).toContain('持久化自建实验');
    expect(html).toContain('<h2>事件</h2>');
    expect(html).not.toContain('开始模拟运行');
  });
  it("resolves encoded sample and step IDs and safely renders standalone log text", () => {
    const exp = findSeed('exp-ds-basic-30k');
    const sample = exp.samples.find(s => s.steps.length > 0)!;
    sample.steps[0].text = '<script>unsafe()</script>';
    vi.stubGlobal('localStorage', { getItem: () => JSON.stringify([exp]) });
    const url = `/traces/${exp.id}/${encodeURIComponent(sample.id)}/steps/${encodeURIComponent(sample.steps[0].id)}`;
    const html = page(h(TraceRecord), url, '/traces/:experimentId/:sampleId/steps/:stepId');
    expect(html).toContain('完整步骤日志');
    expect(html).toContain('&lt;script&gt;unsafe()&lt;/script&gt;');
    expect(html).not.toContain('<script>');
  });
});
