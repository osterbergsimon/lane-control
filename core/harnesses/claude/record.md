# Claude Code

Status: in use

Adapter: `adapter.py`. Orca agent id `claude`; Orca passes `--model` and `--effort`.
- Start: `env LANE_ROLE=<role> LANE_PERSONA=<persona> claude --model <model> [--permission-mode <mode>]` in an Orca
  terminal. Orca recognises a Claude started this way (SPIKE-2, 2026-11-19).
- Persona after compaction: a `SessionStart` hook (matchers `startup|resume|compact`) in the checkout's uncommitted
  `.claude/settings.local.json` runs `../persona-hook.sh`, which prints `$LANE_PERSONA`. Workers started in the same
  checkout do not have the markers, so they get nothing (core/reliability.md 11).
- Idle signal: the newest `*.jsonl` write in `~/.claude/projects/<checkout path, every non-alphanumeric as ->/`.
- Wake: Orca types worker reports into a Claude coordinator (native-orca record, ORCH-1).

## History
- 2026-11-19 adapter (NO-1): start command, persona hook, idle signal.

## Retest when
- Claude Code changes its hook events or its transcript folder layout.

## Trials
None yet as a harness trial (in use for every role since 2026-11-15). The compaction check is a manual check: start a
session with the markers, run `/compact`, and ask for the persona's first line.
