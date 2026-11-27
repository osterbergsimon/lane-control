# lane-control: agent rules

Scripts, personas, skills and written doctrine for running software projects with coding agents (README.md).
Shared doctrine and scripts are in `core/` (`roles.md`, `model-routing.md`, `issues.md`, `reliability.md`,
`machine.md`, `core/scripts/`); everything that depends on one orchestrator is in `orchestrators/<name>/` (record,
setup, notes, adapter, docs, scripts, personas, skills, templates). `scripts/orch` is the command; every other file in
`scripts/` is a link kept at an old path (`core/README.md`). Scripts find the repo root by `.lane-control-root`.

## Checks
- `python3 -m unittest discover -s tests`: must pass before any commit. Fast; stubs Orca and Cotal (`tests/stubs/`).
- `sh -n` / `bash -n` on every shell script you change.
- Never run `tests/test-orca-host-install.sh` unless the issue says so: it wipes a real host user's Orca. Say in your
  report that you did not.

## Rules
- The repo is **public**. No hostnames, IPs, keys, tokens, usernames of real machines or private paths in anything
  you commit; examples use `user@host`, `trial-vps`, `~/code/<project>`.
- Never edit files outside this repo. Tests write only to their own temp dirs: each `tests/test_*.py` imports
  `testhome` first (temp `HOME` and `XDG_*`), takes its own with `testhome.env_for(<temp dir>)`, and never runs a child
  without `HOME` (it falls back to the real one). The suite fails when the real lane-control folders, `~/.agents` or
  the top of a watched package cache change while it runs (`tests/testhome.py` lists them). `LANE_TEST_HOME_GUARD=warn`
  is only for the machine's own writes on a busy machine, after reading each path; CI ignores it.
- Scripts never run agents as root, never copy credentials (`auth.json`, `.credentials.json`, tokens) by hand, and
  never start privileged containers.
- Beads (`bd`, prefix `lc`) lives in `~/code/lane-control-beads`; `.beads` here is a gitignored symlink. Never commit
  `.beads`, never install bd git hooks.
- Commits use the owner's git identity, no tool or AI attribution (no `Co-Authored-By`). No force-push, no
  `git stash`, never switch branches in a shared checkout.
- Personas and skills are prompts for other agents: keep them short, concrete and in plain words; a change to one
  needs a matching check in `tests/test_content.py` when it adds a rule.
- Doctrine files record decisions the owner approved; change one only when the issue says so.
