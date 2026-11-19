# RES-3: the Ante coding agent as a base for lane messaging

Date: 2026-11-19. Beads: lc-jlf. Research only: nothing was installed or configured.

**Question (owner):** Ante is said to be customizable and supported by Orca. Could we build our agent messaging on
it, with messages arriving as a turn and nothing typed into a terminal?

**Short answer: not now.** Ante can be driven without typing, through its protocol server (`ante serve`) over a
Unix socket. But in that mode it does not run in a terminal the owner can watch, and the interactive terminal
session has no way in from outside: there are no extensions or hooks, and its daemon runs inside the TUI
process. Orca supports Ante only as a basic terminal agent: it starts `ante` and types the task into it. Building
lane messaging on Ante means writing our own client for its protocol, which is more work than the problem
deserves today. It may be worth a cheap worker trial later for other reasons (small, fast, many providers).

## 1. What Ante is

| | |
|---|---|
| Repo | github.com/AntigmaLabs/ante (the owner's link), docs at docs.antigma.ai, installer at ante.run |
| Who | Antigma Labs (GitHub org location: United States); the npm SDK lists four maintainers |
| What | A terminal coding agent written in Rust, shipped as one self-contained binary: TUI, headless (`ante -p`), protocol server (`ante serve`), Slack/Discord gateway, local models through a managed llama.cpp |
| License | Repo source is Apache-2.0. The prebuilt `ante` binary is under separate "Binary Preview Terms" (free to use and redistribute unmodified, alpha research preview, may change or stop at any time). The repo holds the SDK, protocol schema, ACP and gateway apps, LLM and exec crates, docs and profiles; **the agent core itself does not appear to be in it**, so in practice we would run a closed binary |
| Maturity | Repo created 2025-12; "Beta preview: expect breaking changes"; releases v0.2.15 (2026-10-24) to v0.3.6 (2026-11-17), roughly two a week; about 2k stars |
| Providers | Anthropic (API key or Claude subscription sign-in), OpenAI (API or Codex sign-in), Gemini, Grok, OpenRouter, Antigma's own hosted "Antix" gateway, local GGUF models; its catalog also lists DeepSeek, Kimi/Moonshot and Qwen, and its headline benchmark runs on DeepSeek |
| Where code goes | To the provider you pick. Separately, **published builds send telemetry by default** (metrics and application logs) to Antigma's telemetry endpoint; `ANTE_TELEMETRY=off` turns it off |

## 2. How customizable it is

Configuration, not code:
- **Settings profiles** (`--profile <name>`, one settings file each) set the whole agent: system prompt (replace
  or append), tool set, skills, memory, effort. Project settings (`.ante/settings.json`) can narrow tools and
  skills but cannot change provider or model or widen permissions. Curated profiles show a stripped four-tool agent
  and a read-only planner.
- **Skills, MCP servers, subagents, `AGENTS.md`, auto-memory** (a per-project `MEMORY.md` index).
- **Permissions** settings and a `--yolo` mode.
- **Protocol and SDKs**: `ante serve` speaks a typed JSONL protocol (operations in, events out) over stdio, a
  Unix socket, or loopback WebSocket with a bearer token; a Rust SDK and a TypeScript SDK (`@antigma/ante-sdk`,
  MIT) drive it; `examples/mini-tui` shows building your own frontend.

What it does **not** have: no extension or plugin API and no hooks. Nothing of ours runs inside the agent loop
the way a Claude hook or a pi/OpenCode plugin does. (No hook mechanism appears in the CLI, settings, protocol or
storage references.)

## 3. Can a message from outside become a turn without typing?

- **In `ante serve` mode: yes.** A client sends `UserInput` (starts a turn) or `Steer` (adds guidance to the
  running turn) as JSON over the socket. That is exactly the injection we want. But each connection drives its
  own session ("each client gets its own connection and at most one active session"), so it is our client, not a
  terminal, that owns the session; there is no shared view of it unless we build one.
- **In the interactive TUI (what Orca starts): no.** The TUI runs the daemon in-process over internal channels;
  it does not open a socket for other clients, and with no hooks or extensions there is nothing that could watch a
  mailbox. The only ways in are typing into the terminal, or an MCP tool the agent calls on its own turn (which
  cannot wake an idle agent).
- Ante's own multi-agent feature, `/term`, opens other agents (Ante, Claude, Codex) in **tmux** and drives them
  by sending prompts to their terminals: typing again.

## 4. Compaction and persona

- Ante compacts automatically when the context reaches a reserve near the window limit; `/compact` (protocol
  `Compact`) takes optional instructions for the summary. Events `CompactStart`/`CompactEnd` carry the summary.
- **The persona survives without help**: put it in the system prompt with `--append-system-prompt`,
  `--system-prompt-file`, or a lane profile's `append_system_prompt`. The system prompt is not compacted, and a
  resumed session restores the prompts it was saved with. One profile per role would do it; no hook needed.

## 5. How Orca starts, tracks and wakes it

From the Orca 1.4.250 app bundle:
- Agent id `ante` (display "Ante"), detected by the `ante` command. Orca launches the bare TUI and injects the
  task **after start through the terminal** (`promptInjectionMode: stdin-after-start`; Orca's note: `ante
  --prompt` is headless and exits, so it starts the TUI and types the prompt).
- Full-access mode adds `--yolo`. No model or effort options: worker-start passes no `--model` for Ante, so it uses
  its own configured model.
- No status hooks: `orca agent hooks status` does not list Ante, and Orca has no injected extension for it (as it
  has for pi), so working/idle comes from Orca's generic terminal tracking, if at all (to confirm in a trial).
- Wake: by typing into the terminal, like every agent.

## 6. Costs and risks

- **Churn.** Beta, "expect breaking changes", about two releases a week, and the binary terms can change.
- **Closed core.** The repo is open but the agent we would run is a prebuilt binary under preview terms, so we
  cannot read or patch what it does, and our Nix setup would wrap a downloaded binary.
- **Telemetry on by default** to a third-party endpoint; must be turned off per lane.
- **Providers in China.** DeepSeek, Kimi/Moonshot and Qwen are in the catalog and DeepSeek is its showcase model.
  A lane profile can pin provider and model (project settings cannot change them), so it is controllable, but the
  defaults lean the wrong way for the owner's rule.
- **Inbound channel security.** `ante serve --sock` lets anything that can open the socket start turns in an agent
  with shell access. A socket under a 0700 lane state dir is fine; never `--ws` beyond loopback.
- **Cost.** The tool is free; it can sign in with a Claude or Codex subscription, but whether that use is within
  the provider's terms is not settled here. API keys cost per token.

## 7. Sketch: lane messaging on Ante, and what it would take

1. Each Ante lane runs `ante serve --sock <lane state>/ante.sock --profile <role>` instead of the TUI.
2. A small bridge per lane (TypeScript SDK or Rust SDK) opens a session, turns lane mail into `UserInput` (idle) or
   `Steer` (busy), and writes the event stream to a log or transcript.
3. For the owner to watch or step in, the bridge would also need a terminal view: our own mini TUI (the repo's
   `examples/mini-tui` is the starting point), run in the Orca terminal.
4. Orca would no longer start the agent itself (`worker-start --agent ante` starts the TUI), so we would launch the
   bridge as a plain terminal command and lose Orca's agent tracking.

That is a small custom harness frontend, not an adapter: weeks, and we would own it against a beta protocol.

**Compared with what we have:** Claude's SendMessage needs no code but is Claude-only on one machine; Orca's
orchestration mail works across agents but types a wake-up line into the terminal. Ante in the TUI is no better
than any other Orca agent (typed wake-ups). Ante in serve mode gives clean injection, but only by giving up the
Orca-managed terminal session the owner watches, unless we build the viewer.

## 8. Recommendation

**Not now for messaging.** Don't build lane messaging on Ante. If we want a cheap, small worker harness with many
providers, give Ante an `untested` record and try it later like codex, with telemetry off and an allowed provider
pinned; that is a separate question from messaging.

What would change the answer:
- Ante gaining hooks or extensions, or letting the interactive TUI listen on a socket (so a second client could
  send `UserInput` into the session the owner sees).
- Orca driving Ante through its protocol (or ACP) instead of the terminal.
- The agent core being published as source, or the binary terms becoming stable.
- Us deciding headless serve-mode workers are fine for some lanes (no owner terminal needed). Then the socket
  bridge alone, without a viewer, is a few days of work and a reasonable trial.

## Sources

- github.com/AntigmaLabs/ante: `README.md`, `BINARY-TERMS.md`, `LICENSE`, `crates/ante-sdk/README.md`,
  `ante-acp/README.md`, `curated/`, releases v0.2.15 to v0.3.6, repo tree; docs source under `docs-site/docs`:
  `reference/protocol-reference.mdx` (ops `UserInput`, `Steer`, `Compact`; Unix socket and WebSocket transports),
  `reference/cli-reference.mdx` (`serve`, prompt flags, `--profile`), `reference/architecture.mdx`,
  `reference/core-concepts.mdx`, `reference/catalog-reference.mdx` (providers), `configuration/preference.mdx`
  (profiles, telemetry), `extend/memory.mdx`, `extend/mcp.mdx`, `extend/subagents.mdx`.
- npm: `@antigma/ante-sdk` 0.4.2 (MIT). GitHub org metadata for AntigmaLabs.
- Orca 1.4.250: `orca orchestration worker-start --help`, `orca agent hooks status`, and the app bundle (agent kind
  `ante`, `tui-agent-config` launch and prompt injection, `--yolo` permission flag, no model options).
