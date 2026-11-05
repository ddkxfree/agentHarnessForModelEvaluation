import type { Outcome, Step } from "../types";

const ansiConflict = "\u001b[31mCONFLICT\u001b[0m (content): Merge conflict in NOTES.md";

function step(partial: Omit<Step, "inputTokens" | "outputTokens" | "durationS"> & {
  durationS?: number | null;
}): Step {
  return {
    ...partial,
    inputTokens: null,
    outputTokens: null,
    durationS: partial.durationS ?? null,
  };
}

function successLog(): Step[] {
  return [
    step({
      id: "ask",
      kind: "message",
      actor: "user",
      text: "按日期区间汇总错误计数，结果写入 /output/summary.txt。",
    }),
    step({
      id: "look",
      kind: "message",
      actor: "model",
      text: "我先列出日志目录，再按题目里的日期过滤。",
    }),
    step({
      id: "ls",
      kind: "tool_call",
      actor: "tool",
      tool: "bash",
      command: "ls logs",
      text: "ls logs",
      durationS: 0.4,
    }),
    step({
      id: "ls-out",
      kind: "tool_result",
      actor: "tool",
      tool: "bash",
      text: "app.log\napp.log.1\n原始行原样保留：<img src=x onerror=alert(1)>",
      durationS: 0.1,
    }),
    step({
      id: "sum",
      kind: "tool_call",
      actor: "tool",
      tool: "bash",
      command: "python summarize.py --from 2024-03-01 --to 2024-03-07",
      text: "python summarize.py --from 2024-03-01 --to 2024-03-07",
      durationS: 1.8,
    }),
    step({
      id: "sum-out",
      kind: "tool_result",
      actor: "tool",
      tool: "bash",
      text: "errors=14\nwrote /output/summary.txt",
      durationS: 0.2,
    }),
    step({
      id: "done",
      kind: "message",
      actor: "model",
      text: "汇总已写入指定文件。错误计数是 14。",
    }),
  ];
}

function budgetGit(): Step[] {
  return [
    step({
      id: "ask",
      kind: "message",
      actor: "user",
      text: "修复损坏的 git 历史并完成合并。不要修改验证脚本。",
    }),
    step({
      id: "status",
      kind: "tool_call",
      actor: "tool",
      tool: "bash",
      command: "git status --short",
      text: "git status --short",
      durationS: 0.3,
    }),
    step({
      id: "status-out",
      kind: "tool_result",
      actor: "tool",
      tool: "bash",
      text: "## HEAD (no branch)\n?? recovered.txt",
      durationS: 0.1,
    }),
    step({
      id: "log",
      kind: "tool_call",
      actor: "tool",
      tool: "bash",
      command: "git log --oneline --all",
      text: "git log --oneline --all",
      durationS: 0.5,
    }),
    step({
      id: "log-out",
      kind: "tool_result",
      actor: "tool",
      tool: "bash",
      text: "b71c0 recovered message\n4aa91 previous tip",
      durationS: 0.2,
    }),
    step({
      id: "merge",
      kind: "tool_call",
      actor: "tool",
      tool: "bash",
      command: "git merge --no-edit recovered",
      text: "git merge --no-edit recovered",
      durationS: 0.6,
    }),
    step({
      id: "merge-out",
      kind: "tool_result",
      actor: "tool",
      tool: "bash",
      text: `${ansiConflict}\n<<<<<<< HEAD\nold note\n=======\nrecovered note\n>>>>>>> recovered`,
      error: true,
      durationS: 0.2,
    }),
    step({
      id: "stop",
      kind: "message",
      actor: "environment",
      text: "记录在此结束：累计输入 token 达到上限 30000。冲突还没有处理，之后没有新的工具调用。",
      failureCause: true,
    }),
  ];
}

function envSqlite(): Step[] {
  return [
    step({
      id: "prep",
      kind: "message",
      actor: "environment",
      text: "开始准备 sqlite-db-truncate 的运行环境。",
      durationS: 0.2,
    }),
    step({
      id: "fail",
      kind: "message",
      actor: "environment",
      text: "拉取任务镜像失败：演示网络不可达。容器没有启动。时间线上没有模型消息，也没有工具调用。",
      error: true,
      failureCause: true,
      durationS: 3.4,
    }),
  ];
}

function longCertLog(): string {
  const lines = [
    "openssl req -x509 -newkey rsa:2048 -keyout key.pem -out cert.pem -days 30 -nodes",
    "Generating a RSA private key",
    "....................+++++ ",
    "writing new private key to 'key.pem'",
    "subject=CN = demo.example, O = Eval Studio",
    "notBefore=Nov  5 00:00:00 2026 GMT",
    "notAfter=Dec  5 00:00:00 2026 GMT",
    "SHA256 Fingerprint=DE:MO:00:11:22",
    "X509v3 Basic Constraints: CA:FALSE",
    "X509v3 Key Usage: Digital Signature, Key Encipherment",
    "-----BEGIN CERTIFICATE-----",
    "MIIDdTCCAl2gAwIBAgIUDEMOONLYNOTAREALCERT0001",
    "this line is padding so the viewer must scroll or expand",
    "the certificate body is truncated in this demo on purpose",
    "-----END CERTIFICATE-----",
    "wrote cert.pem and key.pem",
  ];
  return lines.join("\n");
}

function successCert(): Step[] {
  return [
    step({
      id: "ask",
      kind: "message",
      actor: "user",
      text: "生成自签名证书，主体 CN=demo.example，有效期 30 天。",
    }),
    step({
      id: "cmd",
      kind: "tool_call",
      actor: "tool",
      tool: "bash",
      command:
        "openssl req -x509 -newkey rsa:2048 -keyout key.pem -out cert.pem -days 30 -nodes -subj /CN=demo.example",
      text: "openssl req -x509 -newkey rsa:2048 -keyout key.pem -out cert.pem -days 30 -nodes -subj /CN=demo.example",
      durationS: 2.4,
    }),
    step({
      id: "out",
      kind: "tool_result",
      actor: "tool",
      tool: "bash",
      text: longCertLog(),
      durationS: 0.3,
    }),
    step({
      id: "done",
      kind: "message",
      actor: "model",
      text: "证书和私钥已写好。我没有改验证脚本。",
    }),
  ];
}

function generic(taskId: string, outcome: Outcome): Step[] {
  if (outcome === "env_error") {
    return [
      step({
        id: "prep",
        kind: "message",
        actor: "environment",
        text: `准备 ${taskId} 失败。没有模型消息，也没有工具调用。`,
        error: true,
        failureCause: true,
      }),
    ];
  }
  if (outcome === "budget_exhausted") {
    return [
      step({
        id: "ask",
        kind: "message",
        actor: "user",
        text: `完成任务 ${taskId}。`,
      }),
      step({
        id: "cmd",
        kind: "tool_call",
        actor: "tool",
        tool: "bash",
        command: "ls",
        text: "ls",
        durationS: 0.2,
      }),
      step({
        id: "stop",
        kind: "message",
        actor: "environment",
        text: "达到 token 预算，记录停止。",
        failureCause: true,
      }),
    ];
  }
  if (outcome === "wrong") {
    return [
      step({
        id: "ask",
        kind: "message",
        actor: "user",
        text: `完成任务 ${taskId}。`,
      }),
      step({
        id: "reply",
        kind: "message",
        actor: "model",
        text: "我提交了一份结果，但验证没有通过。",
      }),
      step({
        id: "check",
        kind: "tool_result",
        actor: "tool",
        tool: "verifier",
        text: "expected value mismatch",
        error: true,
        failureCause: true,
        durationS: 0.8,
      }),
    ];
  }
  return [
    step({
      id: "ask",
      kind: "message",
      actor: "user",
      text: `完成任务 ${taskId}。`,
    }),
    step({
      id: "reply",
      kind: "message",
      actor: "model",
      text: "我按题目把结果写到了指定位置。",
    }),
    step({
      id: "check",
      kind: "tool_result",
      actor: "tool",
      tool: "verifier",
      text: "checks passed",
      durationS: 0.6,
    }),
  ];
}

export function stepsFor(taskId: string, outcome: Outcome): Step[] {
  if (taskId === "log-summary-date-ranges" && outcome === "correct") return successLog();
  if (taskId === "fix-git" && outcome === "budget_exhausted") return budgetGit();
  if (taskId === "sqlite-db-truncate" && outcome === "env_error") return envSqlite();
  if (taskId === "openssl-selfsigned-cert" && outcome === "correct") return successCert();
  return generic(taskId, outcome);
}

export function evidenceFor(taskId: string, outcome: Outcome): string {
  if (taskId === "sqlite-db-truncate" && outcome === "env_error") {
    return "环境异常。评分不适用。未发生模型调用，token 与费用均为不适用，不是 0。";
  }
  if (taskId === "fix-git" && outcome === "budget_exhausted") {
    return "验证没有跑完。记录到的 reward 为 0。结束原因是 token 预算，不是环境异常，也不单列为模型答错。";
  }
  if (outcome === "correct") return "验证脚本通过。分数 1 表示这道题通过，分母仍按实验的计划样本计算。";
  if (outcome === "wrong") return "验证脚本未通过。这是模型结果失败，环境是就绪的。";
  if (outcome === "env_error") return "环境没有准备好。评分不适用，不能算进模型失败。";
  if (outcome === "budget_exhausted") return "预算耗尽。reward 记 0，但不归入模型答错。";
  if (outcome === "cancelled") return "用户取消。未完成的样本不记为模型失败。";
  return "没有可展示的评分证据。";
}
