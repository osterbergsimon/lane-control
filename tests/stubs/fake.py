#!/usr/bin/env python3
"""Stand-in for the `orca` and `cotal` CLIs in project-setup tests.

Invoked through symlinks named orca/cotal. Appends `<tool> <args>` to $STUB_LOG (and the argv as JSON to
$STUB_LOG.argv) and answers from the JSON state in
$STUB_STATE (created with defaults on first use). Only the calls project-setup makes are modelled.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

tool = "orca" if Path(sys.argv[0]).name.startswith("orca") else Path(sys.argv[0]).name
args = sys.argv[1:]
state_path = Path(os.environ["STUB_STATE"])
state = json.loads(state_path.read_text()) if state_path.exists() else {}
state.setdefault("orca_up", True)
state.setdefault("repos", [])
state.setdefault("worktrees", [])
state.setdefault("services", {})
state.setdefault("broker", False)
state.setdefault("agents", [])
state.setdefault("terminals", [])
with open(os.environ["STUB_LOG"], "a") as log:
    log.write(tool + " " + " ".join(args) + "\n")
with open(os.environ["STUB_LOG"] + ".argv", "a") as log:  # the same call with its words kept apart
    log.write(json.dumps([tool, *args]) + "\n")


def out(obj, code=0):
    state_path.write_text(json.dumps(state))
    print(json.dumps(obj))
    sys.exit(code)


def opt(name, default=None):
    return args[args.index(name) + 1] if name in args else default


def orca():
    cmd = " ".join(a for a in args[:2])
    if args[:1] == ["status"]:
        if not state["orca_up"]:
            out({"ok": False, "error": {"message": "runtime not reachable"}}, 1)
        out({"ok": True, "result": {"runtime": {"state": "ready", "reachable": True}}})
    if cmd == "repo list":
        out({"ok": True, "result": {"repos": state["repos"]}})
    if cmd == "repo add":
        repo = {"id": f"repo-{len(state['repos']) + 1}", "path": opt("--path")}
        state["repos"].append(repo)
        out({"ok": True, "result": {"repo": repo}})
    if cmd == "skills install":
        Path(os.environ["HOME"], ".agents", "skills", "orca-cli").mkdir(parents=True, exist_ok=True)
        out({"ok": True, "result": {"installed": ["orca-cli"]}})
    if cmd == "worktree list":
        rows = [{"id": f"{w['repo']}::{w['path']}", "displayName": w["name"], "path": w["path"],
                 "parentWorktreeId": w["parent"]} for w in state["worktrees"]]
        out({"ok": True, "result": {"worktrees": rows}})
    if cmd == "worktree create":
        repo = opt("--repo").split(":", 1)[1]
        name = opt("--name")
        parent_path = opt("--parent-worktree", "").split(":", 1)[-1]  # none with --no-parent (a lane in another repo)
        path = str(Path(os.environ["HOME"]) / "orca" / "workspaces" / name)
        Path(path).mkdir(parents=True, exist_ok=True)
        w = {"repo": repo, "name": name, "path": path, "parent": f"{repo}::{parent_path}" if parent_path else None}
        state["worktrees"].append(w)
        if state.get("setup_tab"):
            # A repo setup script: Orca runs it in a "Setup" tab; our scripts end by writing the marker.
            tab = {"handle": f"term_{sum(t['handle'].startswith('term_') for t in state['terminals']) + 1}", "title": path, "paneTitle": "Setup", "worktreePath": path}
            if state.get("setup_tab") == "runner":  # real Orca, before paneTitle is set
                tab = {k: v for k, v in dict(tab, title="bash /x/orca/setup-runner.sh").items() if k != "paneTitle"}
            state["terminals"].append(tab)
            Path(path, ".git").mkdir(exist_ok=True)
            Path(path, "orca.yaml").write_text("scripts:\n  setup: |\n    touch \"$(git rev-parse --git-dir)/orca-setup-done\"\n")
            if not state.get("setup_never_done"):
                Path(path, ".git", "orca-setup-done").write_text("")
        # Orca opens a plain shell in every new worktree.
        shell = {"handle": f"term_{sum(t['handle'].startswith('term_') for t in state['terminals']) + 1}", "title": f"dev@box: {path}", "worktreePath": path}
        shell["preview"] = state.get("shell_preview", "user@box ~/wt $")
        state["terminals"].append(shell)
        out({"ok": True, "result": {"worktree": {"id": f"{repo}::{path}", "path": path,
                                                 "parentWorktreeId": w["parent"]}}})
    if cmd == "terminal list":
        out({"ok": True, "result": {"terminals": state["terminals"]}})
    if cmd == "terminal close":
        state["terminals"] = [t for t in state["terminals"] if t["handle"] != opt("--terminal")]
        out({"ok": True, "result": {}})
    if cmd == "terminal create":
        t = {"handle": f"term_{sum(t['handle'].startswith('term_') for t in state['terminals']) + 1}", "title": opt("--title", "shell"),
             "worktreePath": opt("--worktree").split(":", 1)[1]}
        state["terminals"].append(t)
        command = opt("--command")
        if command and not state.get("no_exec"):
            # Like Orca: the command runs in the new terminal's shell (here at once, to its end). It may call this
            # stub itself, so the state is written first and read back after.
            state_path.write_text(json.dumps(state))
            subprocess.run(["sh", "-c", command], cwd=t["worktreePath"])
            state.clear()
            state.update(json.loads(state_path.read_text()))
            for x in state["terminals"]:
                if x["handle"] == t["handle"]:
                    if state.get("retitle"):  # the shell or the program renames the tab, as fish and OpenCode do
                        x["title"] = state["retitle"]
                    if not state.get("agent_exits"):  # Orca marks a terminal whose agent runs
                        x["agentIdentity"] = "claude"
        out({"ok": True, "result": {"terminal": t}})
    if cmd == "orchestration run-create":
        if state.get("run_create_fails"):
            out({"ok": False, "error": {"message": "stub: run-create failed"}}, 1)
        run = {"runId": f"run_{len(state.setdefault('runs', [])) + 1:012x}", "objective": opt("--objective")}
        state["runs"].append(run)
        out({"ok": True, "result": {**run, "next": []}})  # Orca 1.4.250's concise shape
    if cmd == "orchestration run-list":
        out({"ok": True, "result": {"runs": state.get("runs", [])}})
    if cmd == "orchestration send":
        state.setdefault("sent", []).append({"to": opt("--to"), "subject": opt("--subject"), "body": opt("--body")})
        for key, text in (state.get("handover_on_send") or {}).items():  # sessions write their handovers when asked
            Path(os.environ["HOME"], key).write_text(text)  # (the tests' bd stub reads $HOME/<key>)
        out({"ok": True, "result": {"message": {"id": f"msg_{len(state['sent'])}"}}})
    out({"ok": False, "error": {"message": f"stub: unhandled orca {args}"}}, 9)


def cotal():
    if args[:1] == ["--version"]:
        print("cotal-ai 0.145.1")
        sys.exit(0)
    if args[:2] == ["ext", "list"]:
        print("@cotal-ai/orca  0.145.1  added  runtime:orca" if state.get("orca_ext", True) else "@cotal-ai/web")
        sys.exit(0)
    if args[:1] == ["telemetry"]:
        sys.exit(0)
    if args[:2] == ["space", "create"]:
        # Like cotal 0.145: registers name -> root; .cotal/space.<hex> appears only once the stack starts.
        state.setdefault("spaces", {})[args[2]] = opt("--root")
        Path(opt("--root"), ".cotal").mkdir(parents=True, exist_ok=True)
        out({"outcome": "ok"})
    if args[:2] == ["space", "show"]:
        root = state.get("spaces", {}).get(args[2])
        if root is None:
            out({"outcome": "refused", "result": None, "error": {"code": "TARGET_UNAVAILABLE"}}, 1)
        out({"outcome": "ok", "result": {"name": args[2], "root": root}})
    if args[:1] == ["setup"]:
        if state.get("setup_fails"):
            state["setup_fails"] -= 1
            out({"outcome": "refused", "error": "stub: setup failed"}, 1)
        out({"outcome": "ok"})
    if args[:1] == ["up"]:
        state["broker"] = True
        state["services"].setdefault("stack-broker", "running")
        out({"outcome": "ok"})
    if args[:1] == ["svc"] or args[:1] == ["service"]:
        sub = args[1] if len(args) > 1 and not args[1].startswith("-") else None
        if sub is None:
            svcs = [{"name": n, "state": s} for n, s in state["services"].items()]
            out({"outcome": "ok", "result": {"kind": "service-inventory", "services": svcs}})
        if sub == "add":
            state["services"][args[2]] = "stopped"
            out({"outcome": "ok"})
        if sub == "start":
            state["services"][args[2]] = "running"
            state[f"{args[2]}_path"] = os.environ["PATH"]  # what a real service would resolve from its envRefs
            out({"outcome": "ok"})
    if args[:1] == ["ps"]:
        items = [{"name": a["name"], "status": "running", "cwd": a["cwd"], "mesh": "idle", "role": a.get("role")} for a in state["agents"]]
        out({"outcome": "ok", "result": {"kind": "seats", "items": items}})
    if args[:1] == ["stop"]:
        state["agents"] = [a for a in state["agents"] if a["name"] != args[1]]
        state["terminals"] = [t for t in state["terminals"] if t["handle"] != f"agent_{args[1]}"]
        out({"outcome": "ok"})
    if args[:1] == ["spawn"]:
        name = args[1]
        state["agents"].append({"name": name, "cwd": opt("--cwd"), "role": opt("--role")})
        # The agent's own tab, as the worst case: nothing marks it as an agent yet (identity lags, no cotal- title).
        state.setdefault("terminals", []).append({"handle": f"agent_{name}", "title": f"{name}",
                                                   "worktreePath": opt("--cwd"), "preview": "starting"})
        print("Agent joined")
        state_path.write_text(json.dumps(state))
        sys.exit(0)
    out({"outcome": "refused", "error": f"stub: unhandled cotal {args}"}, 9)


orca() if tool == "orca" else cotal()
