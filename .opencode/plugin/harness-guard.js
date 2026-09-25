// 可验证交付 harness：OpenCode（执行者）的工具调用守卫。
//
// 项目级插件，只在本仓库内运行的 OpenCode 会话加载，不改用户全局配置。每次 bash / 编辑类工具
// 调用前（以及名字含 merge 的 MCP 工具）交给 harness/command_guard.py 判定（--role implementer：另外禁止编辑判定器与护栏）；
// 拒绝时抛错，OpenCode 会中止这次调用并把理由反馈给模型。
// 真实 OpenCode 中的加载与拦截效果待实测（docs/specs/delivery-harness.md「验证状态」）。
import { spawnSync } from "node:child_process";
import path from "node:path";

const EDIT_TOOLS = new Set(["edit", "write", "patch", "multiedit"]);

export function guard(root, payload) {
  const result = spawnSync(
    "python3",
    [path.join(root, "harness", "command_guard.py"), "--format", "json", "--role", "implementer"],
    { input: JSON.stringify(payload), encoding: "utf8" },
  );
  if (result.error) return; // 没有 python3：交给 git 与服务端两层兜底
  if (result.status === 2) throw new Error(result.stderr.trim());
}

export const HarnessGuard = async ({ directory, worktree } = {}) => {
  const root = worktree ?? directory ?? process.cwd();
  return {
    "tool.execute.before": async (input, output) => {
      const args = output?.args ?? {};
      if (input?.tool === "bash" && typeof args.command === "string") {
        guard(root, { command: args.command });
      } else if (EDIT_TOOLS.has(input?.tool) && typeof args.filePath === "string") {
        guard(root, { file_path: args.filePath });
      } else if (typeof input?.tool === "string" && /merge/i.test(input.tool)) {
        guard(root, { tool_name: input.tool }); // MCP 合并工具：Agent 不自行合并 PR（D4）
      }
    },
  };
};

export default HarnessGuard;
