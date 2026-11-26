#!/usr/bin/env python3
"""Stand-in for the `orca` and `cotal` CLIs in project-setup tests.

Invoked through symlinks named orca/cotal. Appends `<tool> <args>` to $STUB_LOG (and the argv as JSON to
$STUB_LOG.argv) and answers from the JSON state in
$STUB_STATE (created with defaults on first use). Only the calls project-setup makes are modelled.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

tool = "orca" if Path(sys.argv[0]).name.startswith("orca") else Path(sys.argv[0]).name
args = sys.argv[1:]
state_path = Path(os.environ["STUB_STATE"])
state = json.loads(state_path.read_text()) if state_path.exists() else {}
# `--environment <name>`: a call routed to a paired Orca server. A saved environment with a state file of its own
# ("environments": [{"name", "state"}]) answers from that file, as the box's own runtime would.
if tool == "orca" and "--environment" in args:
    i = args.index("--environment")
    env_name, args = args[i + 1], args[:i] + args[i + 2:]
    env = next((e for e in state.get("environments", []) if e.get("name") == env_name), None)
    if args[:2] not in (["environment", "rm"], ["environment", "show"]):
        def _open(port):  # an environment reached through a tunnel answers only while something listens there
            import socket
            with socket.socket() as c:
                c.settimeout(1)
                return c.connect_ex(("127.0.0.1", int(port))) == 0
        if env is None or not env.get("reachable", True) or (env.get("tunnel_port") and not _open(env["tunnel_port"])):
            print(json.dumps({"ok": False, "error": {"code": "environment_unavailable",
                                                     "message": f"stub: environment {env_name} not reachable"}}))
            sys.exit(1)
        if env.get("state"):
            state_path = Path(env["state"])
            state = json.loads(state_path.read_text()) if state_path.exists() else {}
    else:
        args = args + ["--environment", env_name]
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


def stub_offer(seed, n, addr):
    """The link `orca pair` gives in the stub: deterministic from the seed, so a test can look for it everywhere."""
    import base64
    import hashlib
    h = hashlib.sha256(f"{seed}:{n}".encode()).hexdigest()
    payload = {"v": 2, "endpoint": "ws://" + addr, "deviceToken": "dvtok-" + h[:32],
               "publicKeyB64": base64.b64encode(bytes.fromhex(h)).decode(), "pairedDeviceId": "dev_" + h[32:40],
               "scope": "runtime"}
    return "orca://pair?code=" + base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")


def opt(name, default=None):
    return args[args.index(name) + 1] if name in args else default


def end_processes(handle):
    """A closed terminal's processes end: the fake /proc entries the session stub made in it (LANE_PROC_DIR)."""
    proc = os.environ.get("LANE_PROC_DIR")
    if not proc or not Path(proc).is_dir():
        return
    for d in Path(proc).iterdir():
        try:
            env = (d / "environ").read_bytes().split(b"\0")
        except OSError:
            continue  # an entry a test made unreadable
        if f"ORCA_TERMINAL_HANDLE={handle}".encode() in env:
            shutil.rmtree(d)


def orca():
    cmd = " ".join(a for a in args[:2])
    if args[:1] == ["status"]:
        if not state["orca_up"]:
            out({"ok": False, "error": {"message": "runtime not reachable"}}, 1)
        app = {"app": {"running": True, "pid": int(os.environ["STUB_APP_PID"])}} if os.environ.get("STUB_APP_PID") else {}
        out({"ok": True, "result": {**app, "runtime": {"state": "ready", "reachable": True,
                                                **({"appVersion": state["app_version"]} if "app_version" in state else {})}}})
    if cmd == "host list":
        out({"ok": True, "result": {"hosts": [{"kind": "local", "name": "this machine", "id": "local",
                                               "machineName": state.get("machine_name", "deskbox")}]}})
    if args[:1] == ["pair"]:  # an access link for another Orca: made from STUB_PAIR_SEED, so no secret is in the env
        state["pair_calls"] = state.get("pair_calls", 0) + 1
        if os.environ.get("STUB_PAIR_UNAVAILABLE"):
            secret = ""
            if os.environ.get("STUB_PAIR_GUIDANCE_SECRET"):  # an Orca message that repeats an offer's link
                secret = " " + stub_offer(os.environ.get("STUB_PAIR_SEED", "seed"), state.get("pair_rotations", 0),
                                          opt("--address", "192.0.2.10"))
            out({"ok": True, "result": {"available": False, "reason": "network_exposure_failed" + secret,
                                        "guidance": "Runtime bind address is pinned to 127.0.0.1; refusing to widen "
                                                    "to all interfaces." + secret}}, 1)
        if "--rotate" in args:
            state["pair_rotations"] = state.get("pair_rotations", 0) + 1
        addr = opt("--address", "192.0.2.10")
        if os.environ.get("STUB_PAIR_OWN_PORT"):  # an Orca that keeps its own port whatever --address says
            addr = addr.rsplit(":", 1)[0] + ":" + os.environ["STUB_PAIR_OWN_PORT"]
        if os.environ.get("STUB_PAIR_BAD_ENDPOINT"):  # an offer for another address than the one asked for
            addr = "192.0.2.10:" + addr.rsplit(":", 1)[-1]
        url = stub_offer(os.environ.get("STUB_PAIR_SEED", "seed"), state.get("pair_rotations", 0), addr)
        if os.environ.get("STUB_PAIR_GARBLED"):  # not JSON, with the link in it
            state_path.write_text(json.dumps(state))
            print("pairing offer: " + url)
            sys.exit(0)
        out({"ok": True, "result": {"available": True, "scope": "runtime", "pairingUrl": url,
                                    "webClientUrl": "https://web.invalid/#" + url, "endpoint": "ws://" + addr}})
    if cmd == "environment list":
        if os.environ.get("STUB_LIST_READS_STDIN"):  # a CLI that reads whatever its stdin holds
            sys.stdin.read()
        out({"ok": True, "result": {"environments": [{"name": e["name"]} for e in state.get("environments", [])]}})
    if cmd == "environment add":
        if opt("--pairing-code") != "-" or not os.environ.get("STUB_PAIRED_BY_STDIN"):
            out({"ok": False, "error": {"message": "stub: the pairing code must come by stdin (--pairing-code -)"}}, 1)
        state.setdefault("environments", []).append({"name": opt("--name"), "reachable": True})
        out({"ok": True, "result": {"environment": {"name": opt("--name")}}})
    if cmd == "environment rm":
        name = opt("--environment")
        if not any(e["name"] == name for e in state.get("environments", [])):
            out({"ok": False, "error": {"message": f"stub: no environment {name}"}}, 1)
        state["environments"] = [e for e in state["environments"] if e["name"] != name]
        out({"ok": True, "result": {"removed": name}})
    if cmd == "repo list":
        out({"ok": True, "result": {"repos": state["repos"]}})
    if cmd == "repo add":
        repo = {"id": f"repo-{len(state['repos']) + 1}", "path": opt("--path")}
        state["repos"].append(repo)
        out({"ok": True, "result": {"repo": repo}})
    if cmd == "workspace cleanup":  # the preview only (`run` is never modelled: it would remove worktrees)
        if args[2:3] != ["preview"] or state.get("cleanup_fails"):
            out({"ok": False, "error": {"message": "stub: workspace cleanup unavailable"}}, 1)
        rows = []
        for repo in state["repos"]:  # a main workspace per repo, which Orca keeps before it reads any rule
            rows.append({"repoId": repo["id"], "repoName": Path(repo["path"]).name, "displayName": "main",
                         "path": repo["path"], "evaluation": {"action": "keep", "cause": "not-removable"}})
            if state.get("cleanup_cause"):  # and a lane worktree beside it, kept for this cause
                rows.append({"repoId": repo["id"], "repoName": Path(repo["path"]).name, "displayName": "lane",
                             "path": repo["path"] + "-lane", "evaluation": {"action": "keep",
                                                                           "cause": state["cleanup_cause"]}})
        out({"ok": True, "result": {"evaluatedAt": 1, "rows": rows}})
    if cmd == "skills install":
        Path(os.environ["HOME"], ".agents", "skills", "orca-cli").mkdir(parents=True, exist_ok=True)
        out({"ok": True, "result": {"installed": ["orca-cli"]}})
    if cmd == "worktree list":
        rows = [{"id": f"{w['repo']}::{w['path']}", "displayName": w["name"], "path": w["path"],
                 "parentWorktreeId": w["parent"], **{k: w[k] for k in ("branch", "linkedPR", "lastActivityAt") if k in w}}
                for w in state["worktrees"]]
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
        if not state.get("close_keeps_processes"):  # an agent still exiting after its terminal closed
            end_processes(opt("--terminal"))
        out({"ok": True, "result": {}})
    if cmd == "terminal create":
        if state.get("create_fails"):
            out({"ok": False, "error": {"message": "stub: terminal create failed"}}, 1)
        t = {"handle": f"term_{sum(t['handle'].startswith('term_') for t in state['terminals']) + 1}", "title": opt("--title", "shell"),
             "worktreePath": opt("--worktree").split(":", 1)[1]}
        state["terminals"].append(t)
        command = opt("--command")
        if command and not state.get("no_exec"):
            # Like Orca: the command runs in the new terminal's shell (here at once, to its end). It may call this
            # stub itself, so the state is written first and read back after.
            state_path.write_text(json.dumps(state))
            subprocess.run(["sh", "-c", command], cwd=t["worktreePath"],
                           env={**os.environ, "ORCA_TERMINAL_HANDLE": t["handle"]})  # as Orca sets it
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
        run = {"runId": f"run_{len(state.setdefault('runs', [])) + 1:012x}", "objective": opt("--objective"),
               "coordinator_handle": os.environ.get("ORCA_TERMINAL_HANDLE")}  # --full shows it; Orca binds the maker
        state["runs"].append(run)
        out({"ok": True, "result": {**run, "next": []}})  # Orca 1.4.250's concise shape
    if cmd == "orchestration run-list":  # pages as Orca 1.4.250: --limit at most 100, then --cursor nextCursor
        limit, start = int(opt("--limit", "10")), int(opt("--cursor", "0"))
        if not 1 <= limit <= 100:
            out({"ok": False, "error": {"message": "stub: --limit must be 1..100"}}, 1)
        rows = state.get("runs", [])
        page = {"runs": rows[start:start + limit]}
        if start + limit < len(rows):
            page["nextCursor"] = str(start + limit)
        out({"ok": True, "result": page})
    if cmd == "orchestration worker-list":  # Orca's rows, newest first, paged by --cursor (state "workers")
        limit, start = int(opt("--limit", "100")), int(opt("--cursor", "0"))
        rows = [w for w in state.get("workers", []) if not opt("--run") or w.get("runId") in (None, opt("--run"))]
        if state.get("worker_list_fails"):
            out({"ok": False, "error": {"message": "stub: worker-list failed"}}, 1)
        page = {"workers": rows[start:start + limit], "scope": {"source": state.get("worker_scope", "all")}}
        if start + limit < len(rows):
            page["nextCursor"] = str(start + limit)
        out({"ok": True, "result": page})
    if cmd == "orchestration send":
        sent = state.setdefault("sent", [])
        mid = f"msg_{len(sent) + 1}"
        frm = opt("--from") or os.environ.get("ORCA_TERMINAL_HANDLE")
        sent.append({"id": mid, "to": opt("--to"), "from": frm, "subject": opt("--subject"), "body": opt("--body"),
                     "thread_id": mid})
        for key, text in (state.get("handover_on_send") or {}).items():  # sessions write their handovers when asked
            Path(os.environ["HOME"], key).write_text(text)  # (the tests' bd stub reads $HOME/<key>)
        import re
        for needle, answer in (state.get("replies") or {}).items():  # the coordinator answers with `reply`
            if needle in (opt("--subject") or ""):
                code = re.search(r"restart ([0-9a-f]+)", opt("--subject") or "")
                sent.append({"id": f"msg_{len(sent) + 1}", "to": frm, "from": state.get("reply_from") or opt("--to"),
                             "subject": "Re: " + opt("--subject"), "thread_id": mid,
                             "body": answer.replace("{code}", code.group(1) if code else "")})
                if state.get("workers_on_reply") is not None:  # a lane started while the coordinator got ready
                    state["workers"] = state["workers_on_reply"]
        out({"ok": True, "result": {"message": {"id": mid}}})
    if cmd == "orchestration inbox":  # a mailbox, read without marking (Orca's --full row fields)
        rows = [{"id": m["id"], "thread_id": m.get("thread_id"), "from_handle": m.get("from"), "to_handle": m["to"],
                 "subject": m["subject"], "body": m["body"], "sequence": i}
                for i, m in enumerate(state.get("sent", [])) if m["to"] == opt("--terminal")]
        out({"ok": True, "result": {"messages": rows, "count": len(rows)}})
    if cmd == "orchestration task-list":  # --full rows of one Run (state "tasks")
        out({"ok": True, "result": [x for x in state.get("tasks", []) if x.get("run_id") == opt("--run")]})
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
