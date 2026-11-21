# The session record (a stable interface)

Which session is a role's own? A marked session (started with `LANE_ROLE` and `LANE_PERSONA`) writes its id to a
small file, so a reader can tell that session from every other one in the same checkout (a worker, a reviewer, the
owner's own session) and ask its harness how long it has been idle. The night check-in (`core/scripts/night-precheck`)
and `lane-idle` read it; so may anything else on the machine.

## Path
```
$XDG_STATE_HOME/lane-control/sessions/<checkout>/<role>.<harness>
```
- `$XDG_STATE_HOME` when it is set and absolute, else `~/.local/state`.
- `<checkout>`: the real path (symlinks resolved) of the folder the session or server was started in, as one folder
  name: every byte that is not a letter or a digit becomes `-` (`/home/u/code/shop.app` is `-home-u-code-shop-app`;
  a multi-byte character becomes one `-` per byte). A session started in a sub-folder of the checkout records under
  the sub-folder's name and is not found for the checkout.
- `<role>`: `LANE_ROLE`, letters, digits and `-` only (`coordinator`, `machine-coordinator`, ...). `<harness>`: `claude` or
  `opencode`. One file per role and harness, so a role changed from one harness to the other leaves the old file; a
  reader that finds both asks which (`lane-idle --harness`).

## Format
The file is one line: the session id, then a trailing newline (`<id>\n`). The id matches `[A-Za-z0-9_-]{1,200}`.
A reader strips surrounding whitespace and takes anything else (empty, a longer id, a space, a slash, a second line)
as no record (a file that is not text counts too). The file is written whole: the writer makes a dot-file beside it
(`.<role>.<harness>.<pid>`) and renames it into place, so a reader never sees half an id; readers skip dot-files.
Nothing else is in the folder that a reader may rely on. Lane-control never deletes a record.

## When it is written
Only by a marked session, and only when the id and the role fit the rules above; a folder that cannot be written is
skipped without an error, since the record is a convenience and never stops a session.
- Claude: the `SessionStart` hook (`persona-hook.sh`) on every start of the session: `startup`, `resume`, `clear`
  and `compact`. The id is the `session_id` of the hook's JSON; the checkout is `$CLAUDE_PROJECT_DIR`, else the
  hook's working directory.
- OpenCode (v2): `lane-persona.js`, on the first model request of a turn whose session id differs from the one it
  wrote last (once per server process). The id can be a sub-agent session's: the latest session that took a turn
  is recorded, and a reader treats the record's whole family (up to its root, then every sub-agent session below)
  as the role's own.
- A session the marked one starts from its own shell inherits the markers (a `claude -p` one-shot, an OpenCode run) and
  records itself the same way, until the marked session's next write of its own.
- Not written: no markers (every other session of the user), a server started with other XDG folders than the
  reader's (the reader looks in its own), or a harness without an adapter (`codex`, `pi`: no record, no idle signal).

## What a record says
The record names a session; it does not say the session is alive or busy. Idle is the age of the session's newest
write in its harness's own store: Claude, the newest `*.jsonl` of the session in `~/.claude/projects/<checkout>/`
(its sub-agents' included; `$CLAUDE_CONFIG_DIR` when set); OpenCode, the newest `time_updated` of the session's
family in `session_v2` and `session_message` of `$XDG_DATA_HOME/opencode/opencode.db`. A record whose session is not
in the store (removed, never written to) is the same as no record: no record means no marked session.

## How to read it
- In Python, from `core/`: `harnesses.read_session(checkout, role, harness)` returns the id or `None`;
  `harnesses.session_record(...)` is the path; the adapter's `idle_minutes(checkout, now, role)` returns minutes or
  `None`, and raises `OSError` or `sqlite3.Error` when a record or store cannot be read (which is not "no session").
- From a shell: `core/scripts/lane-idle [--checkout DIR] [--role ROLE]` prints the idle minutes (exit 0), or exits 1
  when no marked session is recorded and 2 when it cannot tell.

## How to wait on it
The record is not a notification: nothing is sent when a session goes quiet, and a file cannot be blocked on. To
wait for a session to go idle, ask one process to look, not a loop of your own:
```
lane-idle --checkout <dir> --role <role> --wait <minutes> [--every <sec>] [--timeout <sec>]
```
It looks again every `--every` seconds (30) and prints the minutes once the session has been idle that long (exit 0).
With `--timeout` it gives up (exit 3, naming how idle the session was). Exit 1 and 2 are as above. A caller that is an
agent runs it in the background (Claude Code's `run_in_background`) and ends its turn; the harness wakes it when the
command exits, with no turn spent while it waits. Whether a wake of that kind reaches a given harness's coordinator
is the harness's own record (`<harness>/record.md`); `lane-idle` itself needs only a shell.

## Stability
This page is the contract. A change to the path, the file format or the id pattern is a breaking change: it needs a
new file name (so an old reader finds no record rather than a wrong one), a note here, and an entry in the History
of each harness's `record.md`. `tests/test_harnesses.py` (`SessionRecordInterfaceTests`) and `tests/test_lane_idle.py`
pin it with the real writers. Adding a harness adds a `<role>.<harness>` file name and its own paragraph above.
