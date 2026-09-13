/** Diagnostic extension. Load explicitly with pi -e; never auto-installs. */
import { mkdirSync, writeFileSync, renameSync } from "node:fs";
import { join } from "node:path";
import { randomUUID } from "node:crypto";
import { spawnSync } from "node:child_process";

export default function (pi: any) {
  const output = process.env.SESSION_MANAGER_CAPTURE_DIR;
  const bindingScript = process.env.SESSION_MANAGER_BINDING_SCRIPT;
  if (!output && !bindingScript) return;
  for (const name of ["session_start", "session_info_changed", "agent_start", "agent_end", "agent_settled",
                      "ui_prompt_start", "ui_prompt_end", "session_shutdown"]) {
    pi.on(name, (_event: unknown, ctx: any) => {
      try {
        const firstUser = ctx.sessionManager.getBranch().find((entry: any) => entry.type === "message" && entry.message?.role === "user");
        const content = firstUser?.message?.content;
        const firstText = typeof content === "string" ? content : (Array.isArray(content) ? content.filter((part: any) => part.type === "text").map((part: any) => part.text).join(" ") : "");
        const title = pi.getSessionName?.() || Array.from(firstText.replace(/\s+/g, " ").trim()).slice(0, 80).join("") || `Pi · ${ctx.cwd.split("/").pop()}`;
        const record: Record<string, unknown> = {
          provider: "pi", event: name, session_id: ctx.sessionManager.getSessionId(),
          received_at: new Date().toISOString(),
          title, cwd: ctx.cwd, session_file: ctx.sessionManager.getSessionFile(),
        };
        // An observed terminal ID is a candidate, not verified pane ownership.
        if (process.env.ITERM_SESSION_ID) record.terminal_session_id = process.env.ITERM_SESSION_ID;
        if (name === "agent_settled") record.idle = ctx.isIdle();
        if (bindingScript && process.env.SESSION_MANAGER_PYTHON) {
          spawnSync(process.env.SESSION_MANAGER_PYTHON, [bindingScript, "event"], {
            input: JSON.stringify(record), timeout: 2000, stdio: ["pipe", "ignore", "ignore"],
          });
        }
        if (!output) return;
        mkdirSync(output, { recursive: true, mode: 0o700 });
        const id = randomUUID();
        const temporary = join(output, `${id}.tmp`);
        writeFileSync(temporary, JSON.stringify(record) + "\n", { mode: 0o600, flag: "wx" });
        renameSync(temporary, join(output, `${id}.json`));
      } catch {
        // Diagnostic failures must not affect agent execution.
      }
    });
  }
}
