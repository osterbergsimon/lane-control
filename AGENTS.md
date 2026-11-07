# lane-control: agent rules

Scripts, personas, skills and written doctrine for running software projects with coding agents (README.md).
Most of the repo is prose doctrine (`machine.md`, `reliability.md`, `model-routing.md`, `roles.md`, `issues.md`,
`docs/`); the code is `scripts/`, with personas in `personas/`, skills in `skills/` and templates in `templates/`.

## Checks
- `python3 -m unittest discover -s tests`: must pass before any commit. Fast; stubs Orca and Cotal (`tests/stubs/`).
- `sh -n` / `bash -n` on every shell script you change.
- Never run `tests/test-orca-host-install.sh` or `scripts/test-opencode-sandbox.sh` unless the issue says so: the
  first wipes a real host user's Orca, the second needs a real sandbox. Say in your report that you did not.

## Rules
- The repo is **public**. No hostnames, IPs, keys, tokens, usernames of real machines or private paths in anything
  you commit; examples use `user@host`, `trial-vps`, `~/code/<project>`.
- Never edit files outside this repo. Tests write only to their own temp dirs.
- Scripts never run agents as root, never copy credentials (`auth.json`, `.credentials.json`, tokens) by hand, and
  never start privileged containers.
- Beads (`bd`, prefix `lc`) lives in `~/code/lane-control-beads`; `.beads` here is a gitignored symlink. Never commit
  `.beads`, never install bd git hooks.
- Commits use the owner's git identity, no tool or AI attribution (no `Co-Authored-By`). No force-push, no
  `git stash`, never switch branches in a shared checkout.
- Personas and skills are prompts for other agents: keep them short, concrete and in plain words; a change to one
  needs a matching check in `tests/test_content.py` when it adds a rule.
- Doctrine files record decisions the owner approved; change one only when the issue says so.
