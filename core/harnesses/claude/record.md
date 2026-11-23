# Claude Code

Status: in use

Adapter: `adapter.py`. Orca agent id `claude`; Orca passes `--model` and `--effort`.
- Start: `env LANE_ROLE=<role> LANE_PERSONA=<persona> claude --model <model> [--permission-mode <mode>]` in an Orca
  terminal. Orca recognises a Claude started this way (SPIKE-2, 2026-11-19).
- Persona after compaction: a `SessionStart` hook (matchers `startup|resume|clear|compact`) in the checkout's
  uncommitted `.claude/settings.local.json` runs `../persona-hook.sh`, which prints `$LANE_PERSONA`. Workers
  started in the same checkout do not have the markers, so they get nothing (core/reliability.md 11). The hook is its
  own entry; other entries and hooks, under the same matcher too, are left as they are.
- Beads session hook: `install_beads_hook` adds `bd prime` as its own `SessionStart` entry (same matchers) to the same
  uncommitted file, once; `bd prime --hook-json` does not exist in bd 1.0.4, and `--memories-only` is for hooks that
  fire at compaction only, so the plain command is used. Not added when bd is missing or `bd prime` fails, nor when
  the committed `.claude/settings.json` already has a working `bd prime` hook (two would run it twice). A committed
  `bd` hook that fails is named with the fix and never edited (`core/beads.py` `hook_notes`). Note that `bd init`
  itself writes a `bd prime` hook into the `.claude/settings.json` of the folder it runs in.
- Size: Claude keeps at most 10,000 characters of a hook's output (over that it shows a 2,000-character preview and
  does not ask the session to read the rest). A persona over about 9,800 bytes is printed as a first line telling
  the session to read the whole file now, by path, then as much of it as fits, cut at a line end.
- Idle signal: the coordinator's own session only. The hook, in a marked session, records the session id from its
  JSON input in `${XDG_STATE_HOME:-~/.local/state}/lane-control/sessions/<checkout>/<role>.claude`; idle is the
  newest write of that transcript (`~/.claude/projects/<checkout path, every non-alphanumeric as ->/<id>.jsonl`) and
  of its sub-agents' transcripts (`<id>/subagents/`). Other sessions in the checkout do not count; no record means
  no marked session (`None`). The folder is under `$CLAUDE_CONFIG_DIR` when the caller has it set; a coordinator
  started with a config folder the caller does not know (an Orca-managed profile) reads as `None`. Anything the
  coordinator starts from its own shell inherits the markers (a `claude -p` one-shot, for example) and records
  itself instead, until the coordinator's next start, resume, clear or compaction.
- If lane-control is moved or removed, every Claude session in a set-up checkout shows a (non-blocking) hook error
  at start until `install_persona_hook` runs again and moves the entry to the new path.
- Not verified: how Claude names the transcript folder of a very long path (it may shorten it); the adapter does
  not shorten.
- Wake: Orca types worker reports into a Claude coordinator (native-orca record, ORCH-1).

## History
- 2026-11-19 adapter (NO-1): start command, persona hook, idle signal.
- 2026-11-19 review 1: the 10,000-character cap, other hooks kept, idle from the recorded session only.

- 2026-11-23 away-improve (lc-yzb): while the mode is on, `permissions.deny` in the checkout's `.claude/settings.local.json`
  gets `Bash(gh pr merge:*)` and the push rules for the integration branch (`install_away_denies`), taken out again on
  `lane-away off` (`remove_away_denies`, only what was added, noted in the state folder). They back up away-merge, which
  runs gh itself outside the session's Bash rules; they are a guard against a mistake, not against a determined agent.

## Retest when
- Claude Code changes its hook events or its transcript folder layout.

## Trials

### 2026-11-19 live check (Claude Code 2.1.328)
`LANE_LIVE_CLAUDE=1 python3 -m unittest tests.test_harnesses.ClaudeLiveTests` (opt-in, one `claude -p --model haiku`
run, about 10 s, on the owner's own sign-in; it leaves that one transcript in `~/.claude/projects/`): a temp checkout
with the hook installed by the adapter, started with the markers.
- Persona at start: pass — asked to quote its persona's first line, the session did, word for word (the hook's
  output reached its context).
- Session record: pass — the hook recorded the session id from its JSON input, and that id's transcript exists.
- Idle: pass — `idle_minutes` read that transcript (0 minutes).
- Not checked live: `/compact` and `/clear` (an interactive session). They fire the same `SessionStart` hook with
  another `source`, which the hook does not look at; the matcher lists both.
