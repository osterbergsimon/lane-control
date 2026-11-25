# lane-control pieces that overlap Orca (ORC-1, lc-5rz) — WIP

WIP: research notes only, gathered before the Orca 1.4.268 -> 1.4.274 upgrade. The table, the doctrine line and the
follow-up issues are not written yet.

Sources read so far, read-only: Orca 1.4.268's `orca-ide --help` and the help of `pair`, `host list|rm`,
`vm recipe doctor`, `vm env doctor`, `account add|list|select`, `status`, `environment add`, `automations
create|list|runs`, `orchestration worker-start|worker-list|worker-show|check|send|gate-create`, `skills
install|share|installed|update`, `agent hooks`, `agent installed`, `worktree ps`, `screenshot`, `set media`,
`set device`, `tab create`, `workspace cleanup preview`, `diagnostics memory`; `skills get orchestration` (and its
references), `skills get orca-cli`, `skills list`, `agent-context`; release notes v1.4.269, 271, 272, 273, 274
(there is no v1.4.270 release).

## Notes per area (to become table rows)
- **Coordinator notices (worker health).** 269 #46155: coordinator told when a worker's brief may not have been
  submitted (272 #47087 says why). 271 #46296: told when a terminal Claude worker hits its usage limit; 271 #46448:
  told when a worker's agent exits but its terminal stays open. 272 #46628: told when a worker's model retries or
  gives no reply for 10 minutes; 272 #47081 then #47355: a worker stuck on its own background task, after 20 min
  (was 60). 272 #46815: worker exits and model waits missed on headless hosts, restarts and moved panes are
  reported. 273 #47947: "say what is known when a worker goes quiet". These are about workers, not about an idle
  coordinator, which is what night-precheck / lane-idle / the night manager cover.
- **Worker recovery.** 272 #46643: terminal workers a reboot or terminal-daemon crash took resume under
  "Resume work on restart". Overlaps parts of remote.md "A dispatch that can no longer report" (unclear how far).
- **Concurrency.** 1.4.268's orchestration guide: "Start independent workers at the same time, up to about 4 at
  once" (advice, not a limit). Nested worker depth is a setting (default 1, record.md). No machine-wide memory/CPU or
  heavy-job limit found in Orca's help: lane-heavy and lane-room have no Orca counterpart. Note: lane-room counts lane
  agents by `COTAL_ROLE`, so on native Orca it counts none; `orchestration worker-list` (terminal accounting) could.
- **Messaging.** `send` 1.4.268 has no `--on-client`; not in the 269-274 notes read so far either (unclear).
  272 #46842 "agents on a paired server message agents on this computer"; #46757/#46797/#46826 are marked inert.
- **SSH / pairing.** `orca pair` (hidden from top-level help) prints an access link; "The first network pairing
  makes a loopback-only Orca listen on every interface". `host list` lists SSH targets and paired servers; no `host
  add` in the CLI (SSH targets come from Settings or ~/.ssh/config import). `project setup-clone|setup-existing-folder
  --host` make a project available on a host. `environment add` still takes the code only as an argument.
- **Environments.** `vm recipe doctor` validates `environmentRecipes` in orca.yaml; `orca-per-workspace-env` skill.
  `vm env doctor` checks CLIs, agent sign-in, remote reachability, dry-run push, the orca.yaml setup script, disk.
- **Automations.** `--precheck`, `--trigger`, `--setup run|skip`, `--extra-agent-args` (model/effort for Claude);
  272 #47189 "one-command pull request watch with a default deadline" (automations).
- **Accounts.** `account add|list|select|rm` (claude, codex, opencode, devin, antigravity).
- **Skills.** `skills install` writes Orca's bundled skills into `~/.agents/skills` and links them per agent;
  `skills share` publishes installed skills behind an unlisted link (default-off setting).
- **Browser.** `tab create --headless`, `screenshot`, `set media --color-scheme`, `set device`: Orca's own browser.

## Resume
Next: finish reading Orca help still unread (`agent hooks` subcommands, `orca serve --recipe-json`, `ports`,
`worktree create --setup`), then write the table (one row per overlap, replace/wrap/keep), the doctrine line in
README.md "Doctrines" with links from native-orca notes, and file one bd issue per replace/wrap.
