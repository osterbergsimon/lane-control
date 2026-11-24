#!/usr/bin/env python3
"""Stand-in for headless `claude -p` and `opencode2 run` in project-setup tests; never starts a real agent.

Invoked through a symlink named claude, opencode or opencode2. Appends `<tool> -p --model <model>` (claude) or
`<tool> run --model <model>` (OpenCode) to $STUB_LOG and one JSON line (tool, argv, prompt, the files in .lane-control,
cwd, and for OpenCode the environment, the config and the files it got) to $STUB_LOG.agents. Claude takes the prompt as its last
argument, OpenCode on stdin. Each call takes the next step queued for its model of $STUB_STATE (key agent_steps):
{"write": {path: text}, "commit": message, "say": stdout text, "err": stderr text, "exit": code}. For OpenCode the
output is `--format json` events: "say" is one text event; "steps": n adds n tool-call steps before it (default: 1
when the step writes, else 0); "error": message adds an error event.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

tool = Path(sys.argv[0]).name
args = sys.argv[1:]
opencode = tool in ("opencode", "opencode2")
model = args[args.index("--model") + 1]
state_path = Path(os.environ["STUB_STATE"])
state = json.loads(state_path.read_text()) if state_path.exists() else {}
steps = state.setdefault("agent_steps", {}).setdefault(model, [])
step = steps.pop(0) if steps else {}
state_path.write_text(json.dumps(state))
prompt = sys.stdin.read() if opencode else args[-1]
with open(os.environ["STUB_LOG"], "a") as log:
    log.write(f"{tool} {'run' if opencode else '-p'} --model {model}\n")
row = {"tool": tool, "model": model, "argv": args, "prompt": prompt, "cwd": os.getcwd(),
       "lane_files": sorted(p.name for p in Path(".lane-control").glob("*"))}
if opencode:
    row["env"] = {k: os.environ.get(k) for k in ("OPENCODE_CONFIG", "OPENCODE_CONFIG_CONTENT",
                                                 "OPENCODE_DISABLE_PROJECT_CONFIG", "LANE_ROLE", "LANE_PERSONA",
                                                 "LITELLM_API_KEY")}
    # the config as OpenCode reads it: the file OPENCODE_CONFIG names (project-setup's memfd), else the variable
    row["config"] = (Path(os.environ["OPENCODE_CONFIG"]).read_text() if os.environ.get("OPENCODE_CONFIG")
                     else os.environ.get("OPENCODE_CONFIG_CONTENT"))
    # what the run can see: every path in its folder (project-setup's scratch copy), links marked
    row["files"] = sorted(str(Path(h, n).relative_to(".")) + ("@" if Path(h, n).is_symlink() else "")
                          for h, ds, fs in os.walk(".") for n in fs + [d for d in ds if Path(h, d).is_symlink()])
with open(os.environ["STUB_LOG"] + ".agents", "a") as log:
    log.write(json.dumps(row) + "\n")
for path, text in step.get("write", {}).items():
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(text)
if "commit" in step:
    subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", step["commit"]], check=True)
if "err" in step:
    print(step["err"], file=sys.stderr)
if opencode:
    events = [{"type": "step_start", "part": {"messageID": "msg_0", "type": "step-start"}}]
    for i in range(step.get("steps", 1 if step.get("write") else 0)):  # a write is a tool call
        events.append({"type": "step_finish", "part": {"messageID": f"msg_{i}", "type": "step-finish",
                                                       "reason": "tool-calls"}})
    if "error" in step:
        events.append({"type": "error", "error": {"type": "provider.transport", "message": step["error"]}})
    if step.get("say"):
        events.append({"type": "text", "part": {"messageID": "msg_last", "type": "text", "text": step["say"]}})
    print("\n".join(json.dumps(e) for e in events))
else:
    print(step.get("say", ""))
sys.exit(step.get("exit", 0))
