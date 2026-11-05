import { BrowserRouter, Route, Routes } from "react-router-dom";
import { Shell } from "./components/Shell";
import { BenchmarkDetail, BenchmarkList } from "./pages/Benchmarks";
import { Compare } from "./pages/Compare";
import { Connections } from "./pages/Connections";
import { ExperimentDetail } from "./pages/ExperimentDetail";
import { ExperimentList } from "./pages/Experiments";
import { NewExperiment } from "./pages/NewExperiment";
import { TraceDetail, TraceList, TracePair } from "./pages/Traces";
import { Workbench } from "./pages/Workbench";
import { Page } from "./components/ui";
import { MetricsHelp, TraceRecord } from "./pages/Reference";
import { Link } from "react-router-dom";

export function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<Shell />}>
          <Route index element={<Workbench />} />
          <Route path="benchmarks" element={<BenchmarkList />} />
          <Route path="benchmarks/:benchmarkId" element={<BenchmarkDetail />} />
          <Route path="experiments" element={<ExperimentList />} />
          <Route path="experiments/new" element={<NewExperiment />} />
          <Route path="experiments/:experimentId" element={<ExperimentDetail />} />
          <Route path="traces" element={<TraceList />} />
          <Route path="traces/:experimentId/:sampleId" element={<TraceDetail />} />
          <Route path="compare" element={<Compare />} />
          <Route path="compare/report" element={<Compare report />} />
          <Route path="experiments/:experimentId/report" element={<ExperimentDetail report />} />
          <Route path="traces/:experimentId/:sampleId/evidence" element={<TraceRecord />} />
          <Route path="traces/:experimentId/:sampleId/steps/:stepId" element={<TraceRecord />} />
          <Route path="help/metrics" element={<MetricsHelp />} />
          <Route path="compare/traces" element={<TracePair />} />
          <Route path="connections" element={<Connections />} />
          <Route
            path="*"
            element={
              <Page kicker="Eval Studio" title="没有这个页面">
                <Link to="/">回到工作台</Link>
              </Page>
            }
          />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
