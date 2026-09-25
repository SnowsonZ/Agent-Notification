// 可验证交付 harness：Pi（执行者）的工具调用守卫。
//
// 项目级扩展（.pi/extensions/），只在本仓库内、且项目已被信任时加载：未信任的项目不会加载它，
// 执行时需先信任本项目或加 `pi -a`（docs/specs/delivery-harness.md §4）。每次 bash / 写入类工具
// 以及名字含 merge 的工具调用前交给 harness/command_guard.py 判定（--role implementer：另外禁止
// 编辑判定器与护栏）；拒绝时返回 block，Pi 中止这次调用并把理由反馈给模型。
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const EDIT_TOOLS = new Set(["write", "edit", "multi_edit", "patch"]);

type Verdict = { block: true; reason: string } | undefined;

export function guard(root: string, payload: Record<string, unknown>): Verdict {
  const result = spawnSync(
    "python3",
    [path.join(root, "harness", "command_guard.py"), "--format", "json", "--role", "implementer"],
    { input: JSON.stringify(payload), encoding: "utf8" },
  );
  if (result.error) return undefined; // 没有 python3：交给 git 与服务端两层兜底
  if (result.status === 2) return { block: true, reason: result.stderr.trim() };
  return undefined;
}

export function check(root: string, toolName: string, input: Record<string, unknown>): Verdict {
  if (toolName === "bash" && typeof input.command === "string") {
    return guard(root, { command: input.command });
  }
  const filePath = input.path ?? input.file_path ?? input.filePath;
  if (EDIT_TOOLS.has(toolName) && typeof filePath === "string") {
    return guard(root, { file_path: filePath });
  }
  if (/merge/i.test(toolName)) {
    return guard(root, { tool_name: toolName }); // 合并类工具：Agent 不自行合并 PR（D4）
  }
  return undefined;
}

export default function harnessGuard(pi: {
  on: (event: string, handler: (event: { toolName: string; input: Record<string, unknown> }) => unknown) => void;
}) {
  pi.on("tool_call", (event) => check(ROOT, event.toolName, event.input ?? {}));
}
