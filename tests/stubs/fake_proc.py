"""A started stub agent "keeps running": its entry in the fake process table (LANE_PROC_DIR), as /proc shows a live
agent. Nothing for a headless run, a start without a lane role, or when the stub state says the agent exits."""
import json
import os
import sys


def keep_running(name: str):
    proc, state = os.environ.get("LANE_PROC_DIR"), os.environ.get("STUB_STATE", "")
    if not proc or not os.environ.get("LANE_ROLE"):
        return
    if os.path.exists(state):
        with open(state) as f:
            if json.load(f).get("agent_exits"):
                return
    entry = os.path.join(proc, str(os.getpid()))
    os.makedirs(entry, exist_ok=True)
    with open(os.path.join(entry, "cmdline"), "wb") as f:
        f.write(b"".join(os.fsencode(a) + b"\0" for a in (name, *sys.argv[1:])))
    with open(os.path.join(entry, "environ"), "wb") as f:
        f.write(b"".join(os.fsencode(f"{k}={v}") + b"\0" for k, v in os.environ.items()))
    os.symlink(os.getcwd(), os.path.join(entry, "cwd"))
