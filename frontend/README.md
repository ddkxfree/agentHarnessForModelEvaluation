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
