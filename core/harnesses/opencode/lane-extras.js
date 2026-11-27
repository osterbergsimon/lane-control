// lane-extras.js: lane-control's OpenCode v2 plugin for the agent-hook and env extras (core/extras.py). Copied into a
// session folder's .opencode/plugins/ by `lane-control project refresh` (the coordinator's checkout) and
// `lane-control lane prepare` (a lane's or reviewer's worktree), with lane-extras.json one folder up, in .opencode/:
//   {"env": {"NAME": "value"},
//    "hooks": [{"event": "PreToolUse" | "PostToolUse", "matcher": "<regex>", "command": "<path>"}]}
// - env: set in the server's own environment at setup, so the commands its shell tool runs see them.
// - PreToolUse: ctx.tool.hook("execute.before", fn) (OpenCode 2.2.1, probed live for lane-away-guard.js). The script
//   gets Claude's hook input on stdin ({hook_event_name, tool_name, tool_input, session_id, cwd}, tool_name in
//   Claude's words: Bash for the shell tool, Edit, Write, Read...); exit 2 stops the call, its stderr the message the
//   model gets, as in Claude. Any other exit lets the call go on.
// - PostToolUse: ctx.tool.hook("execute.after", fn), the same input; it never stops anything. Not verified against a
//   real OpenCode.
// - SessionStart and Stop have no plugin hook in OpenCode v2: those hooks are Claude only, and this file says nothing
//   of them.
// The matcher is Claude's: empty or "*" matches every tool, else a regular expression on the whole tool name. A
// script that cannot run, or a sidecar that cannot be read, never breaks the session: it is said once on stderr.
import { readFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const TOOLS = { shell: "Bash", bash: "Bash", edit: "Edit", write: "Write", read: "Read", glob: "Glob", grep: "Grep",
  webfetch: "WebFetch" };
const said = new Set();
function say(text) {
  if (said.has(text)) return;
  said.add(text);
  try {
    console.error(`lane-extras: ${text}`);
  } catch {
    // never break the session for a message
  }
}

function sidecar() {
  try {
    const f = join(dirname(dirname(fileURLToPath(import.meta.url))), "lane-extras.json");
    const data = JSON.parse(readFileSync(f, "utf8"));
    return data && typeof data === "object" && !Array.isArray(data) ? data : {};
  } catch (err) {
    if (err?.code !== "ENOENT") say(`cannot read lane-extras.json: ${err?.message ?? err}`);
    return {};
  }
}

function matches(matcher, name) {
  if (!matcher || matcher === "*") return true;
  try {
    return new RegExp(`^(?:${matcher})$`).test(name);
  } catch {
    say(`matcher ${JSON.stringify(matcher)} is not a regular expression; the hook runs for no tool`);
    return false;
  }
}

function runHook(hook, event, ev) {
  const name = TOOLS[ev?.tool] ?? String(ev?.tool ?? "");
  if (!matches(hook.matcher, name)) return null;
  const input = JSON.stringify({ hook_event_name: event, tool_name: name, tool_input: ev?.input ?? {},
    session_id: ev?.sessionID ?? "", cwd: process.cwd(),
    ...(event === "PostToolUse" ? { tool_response: ev?.output ?? null } : {}) });
  const r = spawnSync(hook.command, [], { input, encoding: "utf8", timeout: 60000 });
  if (r.error) {
    say(`hook ${hook.command} did not run: ${r.error.message}`);
    return null;
  }
  return r;
}

async function setup(ctx) {
  const cfg = sidecar();
  for (const [k, v] of Object.entries(cfg.env ?? {})) {
    if (typeof v === "string" && /^[A-Za-z_][A-Za-z0-9_]*$/.test(k) && process.env[k] === undefined) process.env[k] = v;
  }
  const hooks = Array.isArray(cfg.hooks) ? cfg.hooks.filter((h) => h && typeof h.command === "string") : [];
  if (!hooks.length || typeof ctx?.tool?.hook !== "function") return undefined;
  const registrations = [];
  const pre = hooks.filter((h) => h.event === "PreToolUse");
  const post = hooks.filter((h) => h.event === "PostToolUse");
  if (pre.length) {
    registrations.push(await ctx.tool.hook("execute.before", async (ev) => {
      for (const h of pre) {
        const r = runHook(h, "PreToolUse", ev);
        if (r && r.status === 2) throw new Error((r.stderr || "").trim() || `blocked by the hook ${h.command}`);
      }
    }));
  }
  if (post.length) {
    registrations.push(await ctx.tool.hook("execute.after", async (ev) => {
      for (const h of post) runHook(h, "PostToolUse", ev);
    }));
  }
  return async () => {
    for (const r of registrations) {
      try {
        await r?.dispose?.();
      } catch {
        // unloading must not fail
      }
    }
  };
}

export default { id: "lane-extras", setup };
