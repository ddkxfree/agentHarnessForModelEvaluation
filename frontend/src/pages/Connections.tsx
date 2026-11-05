import { useState } from "react";
import { agents, models, runtimes } from "../data/catalog";
import { Page } from "../components/ui";
import { useStudio } from "../state/Studio";

type Probe = "idle" | "testing" | "ok" | "fail";

export function Connections() {
  const { reset } = useStudio();
  const [probes, setProbes] = useState<Record<string, Probe>>({});

  function test(id: string, result: Exclude<Probe, "idle" | "testing">) {
    setProbes((current) => ({ ...current, [id]: "testing" }));
    window.setTimeout(() => {
      setProbes((current) => ({ ...current, [id]: result }));
    }, 700);
  }

  return (
    <Page
      kicker="模型与连接"
      title="演示配置"
      lede="凭证只有占位符。连接测试是模拟的，不会保存 API key，也不会请求真实接口。"
      actions={
        <button type="button" className="text-button" onClick={reset}>
          恢复初始演示数据
        </button>
      }
    >
      <section>
        <h2>模型</h2>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>名称</th>
                <th>说明</th>
                <th>凭证</th>
                <th>测试</th>
              </tr>
            </thead>
            <tbody>
              {models.map((model) => (
                <tr key={model.id}>
                  <td>
                    {model.label}
                    {model.alias ? <div className="muted">{model.alias}</div> : null}
                  </td>
                  <td>{model.note}</td>
                  <td>
                    <input value="未保存 · 占位符" disabled aria-label={`${model.label} 凭证占位符`} />
                  </td>
                  <td>
                    <button type="button" onClick={() => test(model.id, "ok")}>
                      模拟测试
                    </button>
                    <div className="muted">{probeText(probes[model.id], "模拟成功。未发送凭证。")}</div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
      <section>
        <h2>Agent</h2>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>名称</th>
                <th>默认环境</th>
                <th>说明</th>
              </tr>
            </thead>
            <tbody>
              {agents.map((agent) => (
                <tr key={agent.id}>
                  <td>{agent.label}</td>
                  <td>{runtimes.find((item) => item.id === agent.runtimeId)?.label}</td>
                  <td>{agent.note}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
      <section>
        <h2>执行环境</h2>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>名称</th>
                <th>说明</th>
                <th>测试</th>
              </tr>
            </thead>
            <tbody>
              {runtimes.map((runtime) => (
                <tr key={runtime.id}>
                  <td>{runtime.label}</td>
                  <td>{runtime.note}</td>
                  <td>
                    <button type="button" onClick={() => test(runtime.id, runtime.id === "harbor" ? "fail" : "ok")}>
                      模拟测试
                    </button>
                    <div className="muted">
                      {probeText(
                        probes[runtime.id],
                        runtime.id === "harbor"
                          ? "模拟失败。原型没有启动 Docker，也没有连接 Harbor。"
                          : "模拟成功。这不代表本机 Docker 可用。",
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="fine">Oracle 环境检查不计为模型成绩，所以不在可选模型里。尚未接入的评测集见评测集页。</p>
        <button type="button" disabled>
          连接真实后端
        </button>
        <p className="fine">未实现。后续只接入已经验证过的 Harness／Harbor 只读结果，不在页面里启动实验。</p>
      </section>
    </Page>
  );
}

function probeText(state: Probe | undefined, done: string): string {
  if (state === "testing") return "正在模拟…";
  if (state === "ok" || state === "fail") return done;
  return "尚未测试";
}
