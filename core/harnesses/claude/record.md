# Claude Code

Status: in use

Adapter: `adapter.py`. Orca agent id `claude`; Orca passes `--model` and `--effort`.
- Start: `env LANE_ROLE=<role> LANE_PERSONA=<persona> claude --model <model> [--permission-mode <mode>]` in an Orca
  terminal. Orca recognises a Claude started this way (SPIKE-2, 2026-11-19).
- Persona after compaction: a `SessionStart` hook (matchers `startup|resume|clear|compact`) in the checkout's uncommitted
  `.claude/settings.local.json` runs `../persona-hook.sh`, which prints `$LANE_PERSONA`. Workers started in the same
  checkout do not have the markers, so they get nothing (core/reliability.md 11). The hook is its own entry; other
  entries and hooks, under the same matcher too, are left as they are.
- Size: Claude keeps at most 10,000 characters of a hook's output (over that it shows a 2,000-character preview and
  does not ask the session to read the rest). A persona over about 9,800 bytes is printed as a first line telling
  the session to read the whole file now, by path, then as much of it as fits, cut at a line end.
- Idle signal: the coordinator's own session only. The hook, in a marked session, records the session id from its
  JSON input in `${XDG_STATE_HOME:-~/.local/state}/lane-control/sessions/<checkout>/<role>.claude`; idle is the
  newest write of that transcript (`~/.claude/projects/<checkout path, every non-alphanumeric as ->/<id>.jsonl`) and
  of its sub-agents' transcripts (`<id>/subagents/`). Other sessions in the checkout do not count; no record means
  no marked session (`None`). Anything the coordinator starts from its own shell inherits the markers (a `claude -p`
  one-shot, for example) and records itself instead, until the coordinator's next start, resume or compaction.
- Wake: Orca types worker reports into a Claude coordinator (native-orca record, ORCH-1).

## History
- 2026-11-19 adapter (NO-1): start command, persona hook, idle signal.
- 2026-11-19 review 1: the 10,000-character cap, other hooks kept, idle from the recorded session only.

## Retest when
- Claude Code changes its hook events or its transcript folder layout.

## Trials
None yet as a harness trial (in use for every role since 2026-11-15). The compaction check is a manual check: start a
session with the markers, run `/compact`, and ask for the persona's first line.
