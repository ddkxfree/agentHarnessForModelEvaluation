import { Link, useParams } from "react-router-dom";
import { Page } from "../components/ui";
import { useExperiment } from "../state/Studio";
import { visibleText } from "../lib/text";

export function MetricsHelp() {
  return <Page kicker="使用说明 / METRICS" title="指标背后的含义" lede="理解数字的范围，再解释实验的变化。" actions={<Link to="/">返回工作台 →</Link>}>
    <nav className="report-links"><a href="#success">成功率</a><a href="#tokens">用量与费用</a><a href="#pairing">实验配对</a><a href="#links">链接与保存</a></nav>
    <article className="reading-page">
      <section id="success"><h2>成功率与结束状态</h2><p>成功率 = 成功样本数 / 计划样本数。未结束、取消、环境异常和缺失样本仍保留在分母中，因此运行中的比例不是最终成绩。</p><p>模型失败、环境异常、预算耗尽和取消分别记录，不把执行环境问题全部归为模型答错。成功率差值使用“百分点”。</p></section>
      <section id="tokens"><h2>Token、费用与耗时</h2><p>输入、输出和缓存用量分别展示。如果缓存 token 已包含在输入中，不再次相加。未记录、不适用和实际为零是不同状态；缺失用量会影响汇总和可比性。</p><p>费用是基于演示价格表的估算，不代表真实账单。具体价格口径在完整实验报告中保留。墙钟时长是实验经过的时间，任务耗时之和在并发时可以更大。</p><p>轨迹步骤包含消息、工具调用与返回，因此步骤数不等于工具调用次数。逐步用量未记录时，不能由样本总量反推。</p></section>
      <section id="pairing"><h2>实验配对与能力变化</h2><p>按任务身份及重复序号匹配，核对任务指纹；环境异常、取消、预算耗尽、缺失及未结束的记录单独列出。不同评测集不做逐题配对。</p><p>模型、Agent、预算、任务范围和版本的差异都会影响结果。配置不一致时不能把所有变化归因于模型。能力标签统计仅描述这批任务，不代表通用能力结论；原型没有显著性检验或置信区间。</p></section>
      <section id="links"><h2>固定链接与数据保存</h2><p>报告和轨迹有独立地址，默认在当前页打开，也提供新标签页入口。原型数据保存在同一浏览器、同一站点地址的本地存储；更换端口、设备、清除存储或重置数据后，自建实验链接可能失效。</p><p>这不是云端分享或不可变报告快照。报告反映当前保存的实验状态；正式后端接入后再提供持久归档和版本化报告。</p></section>
    </article>
  </Page>;
}

export function TraceRecord() {
  const { experimentId, sampleId, stepId } = useParams();
  const experiment = useExperiment(experimentId);
  const sample = experiment?.samples.find(item => item.id === sampleId);
  const step = stepId ? sample?.steps.find(item => item.id === stepId) : null;
  const back = `/traces/${encodeURIComponent(experimentId ?? '')}/${encodeURIComponent(sampleId ?? '')}`;
  if (!sample || (stepId && !step)) return <Page kicker="轨迹记录" title="未找到记录"><Link to="/traces">返回轨迹分析</Link></Page>;
  return <Page kicker="完整记录 / EVIDENCE" title={step ? '完整步骤日志' : '完整评分证据'} lede={`${experiment?.name} · ${sample.taskId}`} actions={<Link to={back}>← 返回轨迹</Link>}>
    <div className="report-links"><span>{sample.endReason}</span><Link to="/help/metrics">指标说明 ↗</Link></div>
    {step?.command ? <pre className="log">{step.command}</pre> : null}
    <pre className="log standalone-log">{visibleText(step ? step.text : sample.evidence || '未记录')}</pre>
  </Page>;
}
