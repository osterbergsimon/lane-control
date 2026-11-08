#!/usr/bin/env python3
"""Stand-in for headless `claude -p` in project-setup tests; never starts a real agent.

Invoked through a symlink named claude. Appends `<tool> -p --model <model>` to $STUB_LOG and one JSON line
(tool, argv, prompt, the files in .lane-control) to $STUB_LOG.agents. Each call takes the next step queued for its model
of $STUB_STATE (key agent_steps): {"write": {path: text}, "say": stdout text, "exit": code}.
"""
import json
import os
import sys
from pathlib import Path

tool = Path(sys.argv[0]).name
args = sys.argv[1:]
model = args[args.index("--model") + 1]
state_path = Path(os.environ["STUB_STATE"])
state = json.loads(state_path.read_text()) if state_path.exists() else {}
steps = state.setdefault("agent_steps", {}).setdefault(model, [])
step = steps.pop(0) if steps else {}
state_path.write_text(json.dumps(state))
with open(os.environ["STUB_LOG"], "a") as log:
    log.write(f"{tool} -p --model {model}\n")
with open(os.environ["STUB_LOG"] + ".agents", "a") as log:
    log.write(json.dumps({"tool": tool, "model": model, "argv": args, "prompt": args[-1],
                          "lane_files": sorted(p.name for p in Path(".lane-control").glob("*"))}) + "\n")
for path, text in step.get("write", {}).items():
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(text)
print(step.get("say", ""))
sys.exit(step.get("exit", 0))
