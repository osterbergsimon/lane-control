// lane-persona.js: lane-control's OpenCode v2 plugin (core/harnesses/opencode/). Installed in OpenCode's plugin
// folder by the opencode harness adapter; it acts only in a server started with LANE_ROLE and LANE_PERSONA set (the
// start command sets both), so every other OpenCode session of the user is left alone.
//
// OpenCode 2.x plugin API: a default export { id, setup }; setup(ctx) registers session hooks.
// - "context": runs as each model request of a turn is built; the persona is added to its system prompt, so
//   compaction never drops it.
// - "compaction": runs as the compaction request is built; the persona is added there too, so the summary is
//   written knowing the role.
// Each turn also records the session's id in
// ${XDG_STATE_HOME:-~/.local/state}/lane-control/sessions/<server folder, each byte not a letter or digit as ->/
// <role>.opencode, so the idle signal (opencode/adapter.py idle_minutes) reads that session and its sub-agent
// sessions and no other in the checkout.
// Installed once per user ($XDG_CONFIG_HOME/opencode/plugins/) or once per project (<checkout>/.opencode/plugins/);
// with both present OpenCode loads both, and the persona is still added once per request (a copy skips a request
// that already carries the persona text).
// The persona file is read on every call, so a refreshed persona is picked up at once. A missing or unreadable file
// adds nothing; the plugin never throws into OpenCode. OpenCode's TUI also loads plugins; it has no session hooks,
// and the plugin does nothing there.
import { mkdirSync, readFileSync, realpathSync, renameSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { isAbsolute, join } from "node:path";

function persona() {
  try {
    const text = readFileSync(process.env.LANE_PERSONA, "utf8");
    return text.trim() ? text : null;
  } catch {
    return null;
  }
}

let recorded = "";

function record(sessionID) {
  try {
    const role = process.env.LANE_ROLE;
    if (typeof sessionID !== "string" || !/^[A-Za-z0-9_-]{1,200}$/.test(sessionID) || sessionID === recorded) return;
    if (!/^[A-Za-z0-9-]+$/.test(role)) return;
    const xdg = process.env.XDG_STATE_HOME;
    const state = xdg && isAbsolute(xdg) ? xdg : join(homedir(), ".local", "state");
    const key = Buffer.from(realpathSync(process.cwd())).toString("latin1").replace(/[^A-Za-z0-9]/g, "-");
    const folder = join(state, "lane-control", "sessions", key);
    mkdirSync(folder, { recursive: true });
    const tmp = join(folder, `.${role}.opencode.${process.pid}`);
    writeFileSync(tmp, sessionID + "\n");
    renameSync(tmp, join(folder, `${role}.opencode`));
    recorded = sessionID;
  } catch {
    // the idle signal is a convenience; never break the session for it
  }
}

async function add(input) {
  try {
    const text = Array.isArray(input?.system) ? persona() : null;
    // The same persona already in this request: another copy of this plugin (user-level and project-level installs
    // are both loaded) added it first. Check and push are one synchronous step, so two copies never both add it.
    if (text && !input.system.some((part) => part?.text === text)) input.system.push({ type: "text", text });
  } catch {
    // never break the session
  }
}

async function turn(input) {
  record(input?.sessionID);
  await add(input);
}

async function setup(ctx) {
  if (!process.env.LANE_ROLE || !process.env.LANE_PERSONA) return undefined;
  if (typeof ctx?.session?.hook !== "function") return undefined;
  const registrations = [];
  registrations.push(await ctx.session.hook("context", turn));
  registrations.push(await ctx.session.hook("compaction", add));
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

export default { id: "lane-persona", setup };
