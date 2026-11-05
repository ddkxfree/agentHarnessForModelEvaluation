# Agent Eval Studio

智能体评测工作台的前端原型。它和 Python 评测核心分开，放在本目录，不另建仓库。

第一版只用演示数据。页面不调用模型，不启动 Docker，不保存 API key，也不连接 Harness 或 Harbor。

## 启动

```bash
cd frontend
npm install
npm test
npm run dev
```

开发服务器地址是 `http://127.0.0.1:5173/`。生产构建：

```bash
npm run build
npm run preview
```

## 页面

侧栏有六个一级入口：工作台、评测集、实验中心、轨迹分析、实验对比、模型与连接。轨迹分析有自己的列表，实验详情里的样本也会打开同一条轨迹。

「连接真实后端」是禁用的。Harbor 的「模拟测试」只在浏览器里显示失败，不会发起连接。


## 总览、报告与说明页

工作台和实验对比先展示摘要，完整明细保留在独立页面：

- `/experiments/:experimentId/report`：完整实验配置、统计、事件与样本。
- `/compare/report?a=实验A&b=实验B`：完整对比；`#configuration` 定位配置差异。
- `/traces/:experimentId/:sampleId/evidence`：评分证据。
- `/traces/:experimentId/:sampleId/steps/:stepId`：完整步骤日志。
- `/help/metrics`：统计口径、配对规则与保存范围。

默认在当前页导航，另有新标签页入口。样本和步骤 ID 必须 URL 编码。
演示状态存入同源 `localStorage`，首次可读取旧 `sessionStorage`；其他标签页通过 storage 事件同步。
报告、独立证据及说明页不推进模拟执行，后台不可见标签也不推进；回到可见的普通工作页面继续模拟。
这不是实际后端调度。数据只在同一浏览器和同一地址下保存，换端口、清空存储或重置后链接可能失效。
报告反映当前状态，不是不可变快照或跨设备分享链接。无真实模型调用。
