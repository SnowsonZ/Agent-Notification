// opencode 受管理会话的事件上报插件（session-manager 收件箱）。
//
// 加载方式：受管理包装器（bin/session-manager opencode）经 OPENCODE_CONFIG 注入
// 本插件路径，叠加在用户全局配置之上，不改用户任何配置；非受管理会话不设该
// 环境变量，本文件不会被加载。事件经 session_binding.py event 入口上报，与
// Kimi hooks 走同一条链路；包装器退出时由 managed_run 兜底发送 SessionEnd。
import { spawn } from "node:child_process";

const RUN_ID = process.env.SESSION_MANAGER_RUN_ID;
const PYTHON = process.env.SESSION_MANAGER_PYTHON;
const BINDING = process.env.SESSION_MANAGER_BINDING_SCRIPT;

function report(event, sessionID, extras = {}) {
  if (!RUN_ID || !PYTHON || !BINDING || !sessionID) return Promise.resolve();
  const payload = JSON.stringify({
    event,
    session_id: sessionID,
    ...extras,
  });
  return new Promise((resolve) => {
    const child = spawn(PYTHON, [BINDING, "event", "--provider", "opencode"], {
      stdio: ["pipe", "ignore", "ignore"],
    });
    child.on("error", () => resolve());
    child.on("exit", () => resolve());
    child.stdin.write(payload);
    child.stdin.end();
  });
}

// SDK Event 联合类型 → 收件箱事件名（与 inbox_store.EVENTS 对齐）。
function translate(type, properties) {
  switch (type) {
    case "session.created":
    case "session.updated": {
      const info = properties?.info ?? {};
      return ["SessionStart", info.id, { session_title: info.title ?? "", cwd: info.directory ?? "" }];
    }
    case "chat.message": {
      const input = properties ?? {};
      return ["UserPromptSubmit", input.sessionID ?? "", {}];
    }
    case "message.updated": {
      const info = properties?.info ?? {};
      if (info.role === "assistant" && info.error) {
        return ["StopFailure", info.sessionID ?? "", {}];
      }
      return null;
    }
    case "session.idle": {
      const input = properties ?? {};
      return ["Stop", input.sessionID ?? "", {}];
    }
    case "permission.updated": {
      const permission = properties ?? {};
      if (permission.type !== "ask") return null;
      return ["PermissionRequest", permission.sessionID ?? "", {}];
    }
    case "permission.replied": {
      const input = properties ?? {};
      return ["PermissionResult", input.sessionID ?? "", {}];
    }
    default:
      return null;
  }
}

export const OpenCodeCapture = async () => {
  if (!RUN_ID) return {}; // 防御：非受管理进程不应加载本插件。
  return {
    async event({ event }) {
      const translated = translate(event?.type, event?.properties);
      if (translated) await report(translated[0], translated[1], translated[2]);
    },
  };
};

export default OpenCodeCapture;
