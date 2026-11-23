// lane-away-guard.js: lane-control's OpenCode v2 plugin for away-improve mode (core/harnesses/opencode/). Installed in
// OpenCode's plugin folder beside lane-persona.js by the opencode harness adapter (install_away_guard). It is the
// OpenCode half of the deny rules Claude coordinators get: while improve mode is on for the project, the coordinator's
// shell tool refuses `gh pr merge` (and a gh api call that merges), and a git push to the project's integration
// branch, and the refusal says to use away-merge. It is a backstop, not a wall: it reads the command text, so a
// determined agent has other routes; the real guards are away-merge, the persona rule and branch protection.
//
// OpenCode 2.2.1 plugin API (probed live): a default export { id, setup }; setup(ctx) registers
// ctx.tool.hook("execute.before", fn); fn(event) runs before every tool call, event = { tool, sessionID, input, ... },
// and throwing from it stops the call, the model getting the message as the tool result. The shell tool is `shell`
// (`bash` in older builds); its input has `command` and an optional `workdir`.
//
// It acts only in a session started with LANE_ROLE=coordinator, and only for a checkout that `lane-improve on`
// recorded (<state>/lane-control/away-denies/<checkout as one folder name>.opencode.json: the project's name and its
// integration branch). Every call reads ~/.config/lane-control/improve.json (LANE_IMPROVE_FILE) and improve-drain.json
// beside it, so it is live with no restart: it starts with `on`, stays while the mode drains after `off` or the end
// time, and lifts at `lane-improve drained <project>`. State it cannot read (missing, malformed: the same rules as
// core/improve.py; tests/test_opencode_guard.py runs both over the same files) means no guard, said once on stderr.
// A restart is needed once for a coordinator already running when this plugin was installed.
//
// A push is judged by its effective repository: the shell call's workdir, a `cd` earlier in the same command, or
// `git -C`. The coordinator's beads backup (`git -C <beads-repo> push`, whose branch is also main) is another
// repository and is never blocked. An explicit push to the integration branch is refused in the project's checkout or
// when the repository cannot be told; a bare push (no refspec, --all, HEAD) only in the project's checkout, where
// the persona keeps the integration branch checked out. A dry run is never refused.
import { existsSync, readFileSync, realpathSync } from "node:fs";
import { homedir } from "node:os";
import { basename, dirname, isAbsolute, join, resolve, sep } from "node:path";

const said = new Set();
function say(reason) {
  if (said.has(reason)) return;
  said.add(reason);
  try {
    console.error(`lane-away-guard: ${reason}`);
  } catch {
    // never break the session for a message
  }
}

// ---- state: the same validation as core/improve.py (_problem) ---------------------------------------------------
const isStr = (x) => typeof x === "string";
const plain = (x) => x !== null && typeof x === "object" && !Array.isArray(x);
const strMap = (x) => plain(x) && Object.values(x).every(isStr);
const strList = (x) => Array.isArray(x) && x.every(isStr);
const ISO = /^\d{4}-\d{2}-\d{2}(?:[T ]\d{2}(?::\d{2}(?::\d{2}(?:\.\d+)?)?)?)?(?:Z|[+-]\d{2}(?::?\d{2})?)?$/;
const timeOk = (v) => isStr(v) && ISO.test(v) && !Number.isNaN(Date.parse(v.replace(" ", "T")));

function problem(s, drain) {
  if (!plain(s)) return "not a JSON object";
  const need = ["started", "end", "limits", "deny_paths", "projects", "unmetered", "branches", "integration"];
  const missing = need.filter((k) => !(k in s));
  if (missing.length) return "missing " + missing.join(", ");
  if (!timeOk(s.started)) return "started is not a time";
  if (s.end !== "until-back" && !timeOk(s.end)) return 'end is neither a time nor "until-back"';
  if (!isStr(s.limits)) return "limits is not text";
  if (!strList(s.deny_paths)) return "deny_paths is not a list of globs";
  if (s.projects !== "all" && !strList(s.projects)) return 'projects is neither a list of names nor "all"';
  if (typeof s.unmetered !== "boolean") return "unmetered is not true or false";
  if (!strMap(s.branches)) return "branches is not {project: branch}";
  if (!strMap(s.integration)) return "integration is not {project: branch}";
  // the optional fields improve.py validates too (a file with a bad one is off there, so it must be off here)
  if ("prs" in s && !(plain(s.prs) && Object.values(s.prs).every((v) => Number.isInteger(v)))) return "prs is not {project: number}";
  if (s.stop_at != null && !(typeof s.stop_at === "number" && s.stop_at > 0 && s.stop_at < 100)) return "stop_at is not a weekly percentage";
  if (s.max_items != null && !(Number.isInteger(s.max_items) && s.max_items >= 1)) return "max_items is not a whole number above 0";
  if (drain) {
    if (!timeOk(s.ended)) return "ended is not a time";
    if (!strList(s.pending)) return "pending is not a list of names";
  }
  return null;
}

function readState(file, drain) {
  let text;
  try {
    text = readFileSync(file, "utf8");
  } catch (e) {
    if (e?.code !== "ENOENT") say(`${file} cannot be read (${e?.code ?? e}); failing open (no guard)`);
    return null;
  }
  let s;
  let why;
  try {
    s = JSON.parse(text);
    why = problem(s, drain);
  } catch {
    why = "not valid JSON";
  }
  if (why) {
    say(`${file} ignored (${why}); failing open (no guard)`);
    return null;
  }
  return s;
}

function improveFile() {
  return process.env.LANE_IMPROVE_FILE || join(homedir(), ".config", "lane-control", "improve.json");
}

function drainFile() {
  return process.env.LANE_IMPROVE_DRAIN_FILE || join(dirname(improveFile()), "improve-drain.json");
}

/** Whether improve mode (or its drain after `off` or the end time) covers the project. */
function active(project) {
  const s = readState(improveFile(), false);
  if (s && (project === null || s.projects === "all" || s.projects.includes(project))) return true;
  const d = readState(drainFile(), true);
  return !!(d && (project === null || d.pending.includes(project)));
}

// ---- the per-checkout record the adapter writes -----------------------------------------------------------------
const BRANCH = /^[A-Za-z0-9][A-Za-z0-9._/-]*$/;

function readRecord(root) {
  const xdg = process.env.XDG_STATE_HOME;
  const state = xdg && isAbsolute(xdg) ? xdg : join(homedir(), ".local", "state");
  const key = Buffer.from(root).toString("latin1").replace(/[^A-Za-z0-9]/g, "-");
  try {
    const r = JSON.parse(readFileSync(join(state, "lane-control", "away-denies", `${key}.opencode.json`), "utf8"));
    if (!plain(r) || !isStr(r.integration) || !BRANCH.test(r.integration)) return null;
    return { integration: r.integration, project: isStr(r.project) ? r.project : null };
  } catch {
    return null;
  }
}

// ---- reading a shell command ------------------------------------------------------------------------------------
const RESERVED = new Set(["{", "}", "!", "if", "then", "do", "else", "elif", "while", "until", "fi", "done", "time"]);
const WRAPPERS = new Set(["sudo", "doas", "env", "command", "exec", "nohup", "nice", "builtin", "xargs", "stdbuf", "timeout"]);
const SHELLS = new Set(["bash", "sh", "zsh", "dash", "ksh"]);

/** Split a command into items: segments of words, subshell open/close, and command substitutions to read in turn. */
function scan(text) {
  const items = [];
  let words = [];
  let cur = null;
  const pushWord = () => {
    if (cur !== null) words.push(cur);
    cur = null;
  };
  const endSeg = () => {
    pushWord();
    if (words.length) items.push({ k: "seg", words });
    words = [];
  };
  const add = (s) => {
    cur = (cur ?? "") + s;
  };
  const closer = (from) => {  // index of the ')' matching a '(' just before `from`, skipping quotes
    let depth = 1;
    for (let j = from; j < text.length; j++) {
      const c = text[j];
      if (c === "\\") j++;
      else if (c === "'") { j = text.indexOf("'", j + 1); if (j < 0) return text.length; }
      else if (c === '"') { for (j++; j < text.length && text[j] !== '"'; j++) if (text[j] === "\\") j++; }
      else if (c === "(") depth++;
      else if (c === ")" && --depth === 0) return j;
    }
    return text.length;
  };
  const backtick = (from) => {
    for (let j = from; j < text.length; j++) {
      if (text[j] === "\\") j++;
      else if (text[j] === "`") return j;
    }
    return text.length;
  };
  let i = 0;
  while (i < text.length) {
    const c = text[i];
    if (c === "\\") {
      if (text[i + 1] === "\n") { i += 2; continue; }
      add(text[i + 1] ?? "");
      i += 2;
    } else if (c === "'") {
      const j = text.indexOf("'", i + 1);
      add(text.slice(i + 1, j < 0 ? text.length : j));
      i = j < 0 ? text.length : j + 1;
    } else if (c === '"') {
      add("");
      i++;
      while (i < text.length && text[i] !== '"') {
        if (text[i] === "\\") { add(text[i + 1] ?? ""); i += 2; }
        else if (text[i] === "$" && text[i + 1] === "(") {
          const j = closer(i + 2);
          items.push({ k: "sub", text: text.slice(i + 2, j) });
          add("$SUB");
          i = j + 1;
        } else if (text[i] === "`") {
          const j = backtick(i + 1);
          items.push({ k: "sub", text: text.slice(i + 1, j) });
          add("$SUB");
          i = j + 1;
        } else { add(text[i]); i++; }
      }
      i++;
    } else if (c === "$" && text[i + 1] === "(") {
      const j = closer(i + 2);
      items.push({ k: "sub", text: text.slice(i + 2, j) });
      add("$SUB");
      i = j + 1;
    } else if (c === "`") {
      const j = backtick(i + 1);
      items.push({ k: "sub", text: text.slice(i + 1, j) });
      add("$SUB");
      i = j + 1;
    } else if (c === "<" && text[i + 1] === "(" ) {
      const j = closer(i + 2);
      items.push({ k: "sub", text: text.slice(i + 2, j) });
      add("$SUB");
      i = j + 1;
    } else if (c === " " || c === "\t") {
      pushWord();
      i++;
    } else if (c === "#" && cur === null) {
      while (i < text.length && text[i] !== "\n") i++;
    } else if (c === ";" || c === "\n" || c === "|") {
      endSeg();
      i++;
    } else if (c === "&") {
      const prev = text[i - 1];
      const redirect = prev === ">" || prev === "<" || text[i + 1] === ">";
      if (redirect) { add(c); i++; } else { endSeg(); i++; }
    } else if (c === "(") {
      endSeg();
      items.push({ k: "open" });
      i++;
    } else if (c === ")") {
      endSeg();
      items.push({ k: "close" });
      i++;
    } else {
      add(c);
      i++;
    }
  }
  endSeg();
  return items;
}

const dropRedirects = (words) => {
  const out = [];
  for (let i = 0; i < words.length; i++) {
    if (/^\d*[<>]/.test(words[i])) {
      if (/^\d*(?:>>?|<|>&|<&|&>>?)$/.test(words[i])) i++;  // the target is the next word
      continue;
    }
    out.push(words[i]);
  }
  return out;
};

const unknownPath = (p) => /[$`*?[\]{}]/.test(p);

/** A directory: { p: absolute path | null (cannot be told) }. */
function cdTo(dir, arg) {
  if (arg === undefined) return { p: homedir() };
  if (arg === "-" || unknownPath(arg) || dir.p === null && !isAbsolute(arg) && !arg.startsWith("~")) return { p: null };
  const target = arg === "~" ? homedir() : arg.startsWith("~/") ? join(homedir(), arg.slice(2)) : arg;
  return { p: resolve(dir.p ?? "/", target) };
}

function real(p) {
  try {
    return realpathSync(p);
  } catch {
    return resolve(p);
  }
}

function inProject(dir, root) {
  if (dir.p === null) return false;
  const r = real(dir.p);
  return r === root || r.startsWith(root + sep);
}

function destOf(spec) {
  let s = spec.replace(/^\+/, "");
  if (s.includes(":")) s = s.slice(s.lastIndexOf(":") + 1);
  return s.replace(/^refs\/heads\//, "");
}

const PUSH_VALUE_OPTS = new Set(["-o", "--push-option", "--receive-pack", "--exec", "--repo"]);

/** The refusal text for one git push (args after `push`), or null. */
function checkPush(args, repo, root, integration) {
  const pos = [];
  let all = false;
  let dry = false;
  for (let i = 0; i < args.length; i++) {
    const a = args[i];
    if (a === "--") { pos.push(...args.slice(i + 1)); break; }
    if (a === "-n" || a === "--dry-run") dry = true;
    else if (a === "--all" || a === "--mirror") all = true;
    else if (PUSH_VALUE_OPTS.has(a)) i++;
    else if (a.startsWith("-") && a.length > 1) { if (/^-[a-zA-Z]*n[a-zA-Z]*$/.test(a)) dry = true; }
    else pos.push(a);
  }
  if (dry) return null;
  const specs = pos.slice(1);
  const dests = specs.map(destOf);
  const mine = inProject(repo, root);
  const unknown = repo.p === null;
  if ((mine || unknown) && dests.includes(integration)) return `a push to ${integration}`;
  const current = specs.length === 0 || dests.includes("HEAD") || dests.some((d) => d.includes("*")) || all;
  if (mine && current) return `a push of the current branch from the project's checkout, which sits on ${integration}`;
  return null;
}

function checkGh(words) {
  const pos = [];
  for (let i = 1; i < words.length; i++) {
    const w = words[i];
    if (w === "-R" || w === "--repo" || w === "--hostname") i++;
    else if (!w.startsWith("-")) pos.push(w);
  }
  if (pos[0] === "pr" && pos[1] === "merge") return "gh pr merge";
  if (pos[0] === "api" && words.some((w) => /\/pulls?\/[^/\s]+\/merge\b|mergePullRequest|enablePullRequestAutoMerge/.test(w)))
    return "a gh api call that merges a PR";
  return null;
}

function checkSegment(words0, dirRef, root, integration, depth) {
  let words = dropRedirects(words0);
  for (;;) {
    while (words.length && (RESERVED.has(words[0]) || /^[A-Za-z_][A-Za-z0-9_]*=/.test(words[0]))) words = words.slice(1);
    if (!words.length || !WRAPPERS.has(basename(words[0]))) break;
    const w = basename(words[0]);
    words = words.slice(1);
    while (words.length && (words[0].startsWith("-") || (w === "env" && /^[A-Za-z_][A-Za-z0-9_]*=/.test(words[0])))) words = words.slice(1);
    if (w === "timeout" && words.length) words = words.slice(1);
  }
  if (!words.length) return null;
  const name = basename(words[0]);
  if (name === "cd" || name === "pushd") {
    const args = words.slice(1).filter((a) => !/^-[LPe@]*$/.test(a) || a === "-");
    dirRef.dir = cdTo(dirRef.dir, args[0]);
    return null;
  }
  if (name === "popd") { dirRef.dir = { p: null }; return null; }
  if (name === "gh") return checkGh(words);
  if (SHELLS.has(name) || name === "eval") {
    const script = name === "eval" ? words.slice(1).join(" ") : (() => {
      const k = words.findIndex((w, i) => i > 0 && /^-[a-z]*c[a-z]*$/.test(w));
      return k >= 0 ? words[k + 1] : null;
    })();
    return script ? analyze(script, { dir: dirRef.dir }, root, integration, depth + 1) : null;
  }
  if (name !== "git") return null;
  let dir = dirRef.dir;
  let gitDir = null;
  let i = 1;
  for (; i < words.length && words[i].startsWith("-"); i++) {
    const w = words[i];
    if (w === "-C") dir = cdTo(dir, words[++i]);
    else if (w === "-c" || w === "--namespace" || w === "--exec-path") i++;
    else if (w === "--git-dir" || w === "--work-tree") { if (w === "--git-dir") gitDir = words[i + 1]; else dir = cdTo(dir, words[i + 1]); i++; }
    else if (w.startsWith("--git-dir=")) gitDir = w.slice(10);
    else if (w.startsWith("--work-tree=")) dir = cdTo(dir, w.slice(12));
  }
  if (gitDir !== null) {
    const g = cdTo(dir, gitDir);
    dir = g.p !== null && basename(g.p) === ".git" ? { p: dirname(g.p) } : { p: null };
  }
  if (words[i] !== "push") return null;
  return checkPush(words.slice(i + 1), dir, root, integration);
}

/** The refusal text for a command, or null. `start` = { dir } is where the command starts. */
function analyze(text, start, root, integration, depth = 0) {
  if (depth > 4) return null;
  const stack = [];
  const ref = { dir: start.dir };
  for (const it of scan(text)) {
    let hit = null;
    if (it.k === "open") stack.push(ref.dir);
    else if (it.k === "close") { if (stack.length) ref.dir = stack.pop(); }
    else if (it.k === "sub") hit = analyze(it.text, { dir: ref.dir }, root, integration, depth + 1);
    else hit = checkSegment(it.words, ref, root, integration, depth);
    if (hit) return hit;
  }
  return null;
}

function refusal(what, integration, project) {
  return `away-improve mode is on for this project, so ${what} is refused here. Merges into the away branch go only ` +
    `through away-merge (core/scripts/away-merge --project ${project ?? "<project>"} --pr <n> --item "<title>"), run by ` +
    `you the coordinator after the review comment; nothing is merged into or pushed to ${integration} while the owner ` +
    "is away. If away-merge refuses, fix what it names; do not go around it.";
}

async function guard(event) {
  let what = null;
  let rec = null;
  try {
    if (event?.tool !== "shell" && event?.tool !== "bash") return;
    const command = event?.input?.command;
    if (typeof command !== "string" || !command.trim()) return;
    const root = real(process.cwd());
    rec = readRecord(root);
    if (!rec) return;
    if (!active(rec.project)) {
      if (!existsSync(improveFile()) && !existsSync(drainFile()))
        say("a guard record is installed but there is no improve state: failing open (no guard); `lane-improve drained <project>` clears it");
      return;
    }
    const wd = event?.input?.workdir;
    const start = { dir: isStr(wd) && wd ? cdTo({ p: root }, wd) : { p: root } };
    what = analyze(command, start, root, rec.integration, 0);
  } catch (e) {
    say(`internal error (${e}); failing open (no guard)`);
    return;
  }
  if (what) throw new Error(refusal(what, rec.integration, rec.project));
}

async function setup(ctx) {
  if (process.env.LANE_ROLE !== "coordinator") return undefined;
  if (typeof ctx?.tool?.hook !== "function") return undefined;
  const registration = await ctx.tool.hook("execute.before", guard);
  return async () => {
    try {
      await registration?.dispose?.();
    } catch {
      // unloading must not fail
    }
  };
}

export default { id: "lane-away-guard", setup };
