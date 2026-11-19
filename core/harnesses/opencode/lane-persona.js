// lane-persona.js: lane-control's OpenCode v2 plugin (core/harnesses/opencode/). Installed in OpenCode's plugin
// folder by the opencode harness adapter; it acts only in a server started with LANE_ROLE and LANE_PERSONA set (the
// start command sets both), so every other OpenCode session of the user is left alone.
//
// OpenCode 2.x plugin API: a default export { id, setup }; setup(ctx) registers session hooks.
// - "context": runs as each model request of a turn is built; the persona is added to its system prompt, so
//   compaction never drops it.
// - "compaction": runs as the compaction request is built; the persona is added there too, so the summary is
//   written knowing the role.
// The persona file is read on every call, so a refreshed persona is picked up at once. A missing or unreadable file
// adds nothing; the plugin never throws into OpenCode. OpenCode's TUI also loads plugins; it has no session hooks,
// and the plugin does nothing there.
import { readFileSync } from "node:fs";

function persona() {
  try {
    const text = readFileSync(process.env.LANE_PERSONA, "utf8");
    return text.trim() ? text : null;
  } catch {
    return null;
  }
}

async function add(input) {
  try {
    const text = Array.isArray(input?.system) ? persona() : null;
    if (text) input.system.push({ type: "text", text });
  } catch {
    // never break the session
  }
}

async function setup(ctx) {
  if (!process.env.LANE_ROLE || !process.env.LANE_PERSONA) return undefined;
  if (typeof ctx?.session?.hook !== "function") return undefined;
  const registrations = [];
  for (const name of ["context", "compaction"]) registrations.push(await ctx.session.hook(name, add));
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
