// lane-persona.js: lane-control's OpenCode plugin (core/harnesses/opencode/). Installed in OpenCode's plugin folder
// by the opencode harness adapter; it acts only in a session started with LANE_ROLE and LANE_PERSONA set (the start
// command sets both), so every other OpenCode session on the machine is left alone.
//
// - experimental.chat.system.transform: adds the persona to the system prompt on every turn, so compaction never
//   drops it.
// - experimental.session.compacting: adds it to the compaction's context too, as a backstop.
// The persona file is read on every call, so a refreshed persona is picked up at once. A missing or unreadable file
// adds nothing; the plugin never throws into OpenCode.
import { readFileSync } from "node:fs";

function persona() {
  const role = process.env.LANE_ROLE;
  const path = process.env.LANE_PERSONA;
  if (!role || !path) return null;
  try {
    const text = readFileSync(path, "utf8");
    return text.trim() ? text : null;
  } catch {
    return null;
  }
}

function append(list) {
  try {
    const text = Array.isArray(list) ? persona() : null;
    if (text) list.push(text);
  } catch {
    // never break the session
  }
}

export const LanePersona = async () => ({
  "experimental.chat.system.transform": async (_input, output) => append(output?.system),
  "experimental.session.compacting": async (_input, output) => append(output?.context),
});
