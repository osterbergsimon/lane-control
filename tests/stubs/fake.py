#!/usr/bin/env python3
"""Stand-in for the `orca` and `cotal` CLIs in project-setup tests.

Invoked through symlinks named orca/cotal. Appends `<tool> <args>` to $STUB_LOG and answers from the JSON state in
$STUB_STATE (created with defaults on first use). Only the calls project-setup makes are modelled.
"""
import json
import os
import sys
from pathlib import Path

tool = Path(sys.argv[0]).name
args = sys.argv[1:]
state_path = Path(os.environ["STUB_STATE"])
state = json.loads(state_path.read_text()) if state_path.exists() else {}
state.setdefault("orca_up", True)
state.setdefault("repos", [])
state.setdefault("worktrees", [])
state.setdefault("services", {})
state.setdefault("broker", False)
state.setdefault("agents", [])
with open(os.environ["STUB_LOG"], "a") as log:
    log.write(tool + " " + " ".join(args) + "\n")


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
        out({"ok": True, "result": {"installed": ["orca-cli"]}})
    if cmd == "worktree list":
        rows = [{"id": f"{w['repo']}::{w['path']}", "displayName": w["name"], "path": w["path"],
                 "parentWorktreeId": w["parent"]} for w in state["worktrees"]]
        out({"ok": True, "result": {"worktrees": rows}})
    if cmd == "worktree create":
        repo = opt("--repo").split(":", 1)[1]
        name = opt("--name")
        parent_path = opt("--parent-worktree").split(":", 1)[1]
        path = str(Path(os.environ["HOME"]) / "orca" / "workspaces" / name)
        Path(path).mkdir(parents=True, exist_ok=True)
        w = {"repo": repo, "name": name, "path": path, "parent": f"{repo}::{parent_path}"}
        state["worktrees"].append(w)
        out({"ok": True, "result": {"worktree": {"id": f"{repo}::{path}", "path": path,
                                                 "parentWorktreeId": w["parent"]}}})
    out({"ok": False, "error": {"message": f"stub: unhandled orca {args}"}}, 9)


def cotal():
    if args[:1] == ["--version"]:
        print("cotal-ai 0.145.1")
        sys.exit(0)
    if args[:1] == ["telemetry"]:
        sys.exit(0)
    if args[:2] == ["space", "create"]:
        root = Path(opt("--root"))
        (root / ".cotal").mkdir(parents=True, exist_ok=True)
        (root / ".cotal" / f"space.{args[2]}").write_text("")
        out({"outcome": "ok"})
    if args[:1] == ["setup"]:
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
            out({"outcome": "ok"})
    if args[:1] == ["ps"]:
        items = [{"name": a["name"], "status": "running", "cwd": a["cwd"], "mesh": "idle"} for a in state["agents"]]
        out({"outcome": "ok", "result": {"kind": "seats", "items": items}})
    if args[:1] == ["spawn"]:
        name = args[1]
        state["agents"].append({"name": name, "cwd": opt("--cwd")})
        print("Agent joined")
        state_path.write_text(json.dumps(state))
        sys.exit(0)
    out({"outcome": "refused", "error": f"stub: unhandled cotal {args}"}, 9)


orca() if tool == "orca" else cotal()
