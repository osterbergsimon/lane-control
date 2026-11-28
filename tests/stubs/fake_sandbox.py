#!/usr/bin/env python3
"""Stand-ins for systemd-run, pasta, bwrap, nsenter and the real agent in the SBX-2 shim tests (lc-35qn). Invoked
through links of those names in a temp bin folder. Each appends one JSON line {tool, argv, env, cwd} to the log that
fake.json (beside the links) names, then does the least the real tool does for the shim:

  systemd-run   `--user --scope -q -- true` is the shim's probe (fails when fake.json says fail_scope); otherwise
                exec what follows `--`
  pasta         exec what follows `--` (the opener)
  bwrap         write {"child-pid", "mnt-namespace"} to --info-fd (unless no_info: exit 1 first), wait for a byte on
                --block-fd, then run the agent as a child, put its pid in the fake /proc's children file, pass on
                signals and exit with its status
  nsenter       exit 1 when fail_nsenter, else 0 (nft is never run)
  opencode2     the agent: record, and on SIGWINCH/SIGTERM append the signal's name to the log; exit agent_rc after
                agent_wait seconds (or at SIGTERM)"""
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

me = Path(sys.argv[0]).name
cfg = json.loads((Path(sys.argv[0]).parent / "fake.json").read_text())


def log(**kw):
    with open(cfg["log"], "a") as f:
        f.write(json.dumps({"tool": me, "argv": sys.argv[1:], "env": dict(os.environ), "cwd": os.getcwd(), **kw})
                + "\n")


def after_dashes(args):
    return args[args.index("--") + 1:]


if me == "systemd-run":
    log()
    rest = after_dashes(sys.argv[1:])
    if rest == ["true"]:
        sys.exit(1 if cfg.get("fail_scope") else 0)
    os.execvp(rest[0], rest)
elif me == "pasta":
    log()
    rest = after_dashes(sys.argv[1:])
    os.execvp(rest[0], rest)
elif me == "nsenter":
    log()
    sys.exit(1 if cfg.get("fail_nsenter") else 0)
elif me == "bwrap":
    log()
    args = sys.argv[1:]
    i = args.index("--block-fd")
    agent = args[i + 2:]
    if cfg.get("no_info"):
        sys.exit(1)
    with os.fdopen(3, "w") as info:
        info.write(json.dumps({"child-pid": cfg["nspid"], "mnt-namespace": cfg["mntns"]}))
    with os.fdopen(4, "rb") as block:
        got = block.read(1)
    if not got:
        log(event="never-released")
        sys.exit(1)
    log(event="released")
    if "--chdir" in args:  # what a real bwrap does; the mounts are not made, so the outbox is named by its host path
        os.chdir(args[args.index("--chdir") + 1])
    for j, x in enumerate(args[:-2]):
        if x == "--bind" and args[j + 2] == "/run/lane/outbox":
            os.environ["LANE_OUTBOX"] = args[j + 1]
    if cfg.get("host_path"):  # the tool path's folders exist inside a real sandbox; here they may be placeholders
        os.environ["PATH"] = os.environ.get("PATH", "") + ":" + cfg["host_path"]
    child = subprocess.Popen(agent)
    (Path(cfg["proc"]) / str(cfg["host_pid"]) / "task" / str(cfg["host_pid"]) / "children").write_text(
        f"{child.pid} ")
    for s in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(s, lambda n, f: child.send_signal(n))
    sys.exit(child.wait())
else:  # the agent
    log(event="agent-start")
    stop = []

    def on(n, f):
        log(event=f"signal {signal.Signals(n).name}")
        if n == signal.SIGTERM:
            stop.append(n)
    signal.signal(signal.SIGWINCH, on)
    signal.signal(signal.SIGTERM, on)
    Path(cfg["log"] + ".agent-ready").write_text(str(os.getpid()))
    end = time.time() + float(cfg.get("agent_wait", 0))
    while time.time() < end and not stop:
        time.sleep(0.05)
    sys.exit(int(cfg.get("agent_rc", 0)))
