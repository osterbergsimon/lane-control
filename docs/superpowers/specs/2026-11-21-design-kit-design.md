# Design kit: retire the design lead, give projects a frontend brief, a design file, a design reviewer and a gallery (design)

Date: 2026-11-21. Status: approved by the owner 2026-11-21 with the change in section 9 (beads lc-jai, JAI-1).

## 1. Intent

The owner, 2026-11-21: retire the long-lived design lead in favour of short-lived agents that read the design skills,
and give projects the tooling and prompts to build and use a component gallery. lane-control ships the kit; it never
builds a project's gallery.

The kit, in five pieces:
1. **Frontend brief addition** (`core/templates/frontend-brief.md`): the coordinator appends it to a lane's brief when
   the lane changes what a user sees.
2. **Design file** (`DESIGN.md` at the project's root, template `core/templates/DESIGN.md`): the project's design
   memory, committed.
3. **Design reviewer** (`orchestrators/native-orca/personas/design-reviewer.md`): a worker run like the code
   reviewer, cold, on a different model, with the verdict `APPROVE` or `BLOCK`.
4. **`component-gallery` skill** (`core/skills/component-gallery/SKILL.md`): how a project builds its gallery and how
   lanes add to it.
5. **`design-shots`** (`core/scripts/design-shots`): headless screenshots at phone and desktop width in both themes,
   and a before/after comparison.

## 2. Where each design-lead job goes

| Design lead today | After |
|---|---|
| Owns the look across time (visual identity, tokens, type) | `DESIGN.md`, read first and updated in the same PR by every frontend lane |
| Its project additions (`.lane-control/additions/design-lead.md`, Cotal: `.lane-control/design-lead.md`): subject, where it lives, design language, how to run it, added floor | Moved into `DESIGN.md` (section 6) |
| The design method: ground it in the subject, token plan, critique the plan, boldness in one place, AI-template tells, quality floor, browser check | The frontend brief addition, which names the skills and keeps the floor and the tells in its own words |
| Does design work in its worktree | Frontend lanes, short-lived, briefed by the coordinator like any lane |
| Opens frontend lanes from its own branch | **Dropped on purpose**: lanes branch from the default branch; a design that spans lanes is one issue with dependencies |
| Reviews frontend lanes cold, phone and desktop, `APPROVE`/`BLOCK` | The design reviewer |
| Runs a code reviewer on its lanes | The coordinator, as for every lane |
| Its own Run, messages, cross-host wakes, handover memory (`handover-design-lead`) | **Dropped**: lanes and reviewers report to the coordinator. No new long-lived session, so the night gap of DAC-1 (night-precheck covers coordinators only) does not come back |
| Tier pin `claude/opus`, its own tier table | Frontend lanes take their tier from the path map; setting a visual direction is "an open design question", so deep (`core/model-routing.md`). The design reviewer is pinned like the reviewer |
| Merge, close-lane, beads, budget, lane-room and heavy rules | The coordinator's, unchanged |
| The owner talks to it in its terminal | **Dropped**: the owner talks to the coordinator, or to a lane's terminal while it runs |
| `--no-design-lead` for projects without a frontend | Not needed: the kit is used only when a lane touches the frontend |

## 3. The design file

`DESIGN.md` at the project's root: the project's own knowledge, read by agents and people, kept without lane-control.
A project that already has a design-system document keeps it; `DESIGN.md` then points to it in one line and holds
the rest. The first frontend lane in a project without one creates it from the template, filling what it can read in
the code and leaving `<!-- unknown -->` for the rest. At most about 150 lines; it links to token files rather than
copying them.

Template sections: **Subject** (who uses it, for what, the main screen's one job) · **Where it lives** (frontend
paths, stack, the gallery and its pages file) · **Tokens** (4-6 named colours with hex, light and dark, or the token
file) · **Type** (families, roles, scale) · **Spacing and layout** (scale, breakpoints, radius and shadow rules) ·
**Components** (one line each: name, path, gallery entry, states) · **Principles** (3-5, this project's) · **Quality
floor, added** (beyond the brief's floor, or "none") · **Seeing it run** (start command on 127.0.0.1, login stub, the
exact `design-shots` line) · **Decisions** (dated one-liners: what changed and why).

## 4. The frontend brief addition

Appended to `templates/lane-brief.md` by the coordinator for any lane that changes what a user sees. In order:
1. Read `DESIGN.md` (create it from the template if missing) and the skills `frontend-design`, `ui-ux-pro-max` and
   `modern-web-guidance` (Claude and OpenCode both have them after setup; section 9). Name any you lack in the
   report; never block on them.
2. Before the first edit: `design-shots` of the gallery (or the pages the issue touches) at the base, into
   `$(git rev-parse --git-dir)/design-shots/before/`.
3. Plan before code: for a new screen or visual direction a token plan (colours, type, spacing, ASCII wireframe,
   principles) in the PR body; otherwise name the tokens and components reused. Critique it against the issue.
4. The floor, in the brief itself: works at 390 px with no horizontal scroll and at 1280 px; visible keyboard focus;
   reduced motion respected; contrast 4.5:1 for text, 3:1 for UI; light and dark; none of the template tells (the
   list from the design-lead persona, kept).
5. Gallery: add or update an entry for each component touched, every state, both themes (the skill). No gallery in
   the project: say `gallery: none`; building one is its own issue, never done unasked.
6. Update `DESIGN.md` in the same PR when a token, component or rule changed (a dated line under Decisions).
7. `design-shots` at the final commit into `.../design-shots/after/`, then `design-shots compare`.
8. The report gets one more line: `design: DESIGN.md updated|unchanged · gallery +<n>|none · shots before|none ·
   skills missing <names>|none`.

## 5. The design reviewer

Rendered into `.lane-control/design-reviewer.md` like the debugger (pinned like the reviewer). Read-only, like the
code reviewer; the coordinator starts it as its own worker in the lane's worktree, on a different model from the
lane's, with the issue, branch, base and exact commit, never the lane's report. Every frontend lane gets one (as with
the design lead today), beside the code reviewer from review tier B up; the two may run at once.

It reads `DESIGN.md`, starts the app the way its "Seeing it run" says (127.0.0.1 only), takes its own `after` shots
at the commit, compares them with the lane's `before` shots (missing: a `minor` finding, and it compares against
`DESIGN.md` and the gallery alone), and checks the floor by hand at 390 and 1280 px, keyboard only, in both themes.
Its shots go under the worktree's git dir, never into the tree. Report shape:

```
design-review <KEY> @<sha7>: APPROVE|BLOCK · blocker <n> · major <n> · minor <n>
shots: <dir> · before <base7>|none · 390, 1280 · light, dark
exercised: <what it ran> | read only: <what it could not run>
- blocker <file>:<line> <what is wrong> -> <what to do>
```
`BLOCK` when there is any `blocker` or `major`; levels as the design lead's (`blocker`: broken, inaccessible or not
what the issue asked; `major`: someone would hit it, or it breaks `DESIGN.md`; `minor`: polish). At most two rounds.

## 6. The gallery skill

`component-gallery`, installed by project-setup into `~/.agents/skills` (with the `DESIGN.md` template) like
`native-orca-lanes`. It says:
- **Framework-native first**: use the project's gallery if it has one (Storybook, Histoire, Ladle, Lookbook,
  Widgetbook, the like). Adding such a tool is a new dependency: only when the issue or the owner says so.
- **Otherwise a plain route**, `/gallery` (or the framework's equivalent), built only in development: every component
  in every state (default, hover, focus, active, disabled, loading, empty, error, long content), each state labelled,
  real tokens, fixed data (no clocks, random or network), no motion that runs by itself. It follows
  `prefers-color-scheme` and also takes `?theme=light|dark`.
- **A pages file** (`gallery.pages`, one `name path` per line) lists what `design-shots` shoots; `DESIGN.md` names it.
- **Lanes add an entry** for each component they add or change, in the same PR, and a line in `DESIGN.md`'s
  Components.

## 7. `design-shots`

```
design-shots shoot <base-url> --out <dir> [--page <path>]... [--pages <file>] [--widths 390,1280]
                   [--themes light,dark] [--wait-for <selector>] [--serve "<command>" [--timeout <s>]]
design-shots compare <before-dir> <after-dir> --out <dir>
```
- `shoot` runs Playwright's `screenshot` command once per page, width and theme: headless, a fresh browser and
  context each time (an isolated profile, nothing of the user's), full page, `--color-scheme` for the theme. Files
  are `<name>--<width>--<theme>.png`, with `shots.tsv` (name, url, width, theme, file, sha256).
- Playwright from `DESIGN_SHOTS_PLAYWRIGHT`, else the project's `node_modules/.bin/playwright`, else `playwright` on
  PATH; it never downloads one or a browser. None found: exit 3 with what to install.
- `--serve` starts the project's server in its own process group, waits for the base URL to answer, and stops the
  group afterwards whatever happens. The base URL must be on 127.0.0.1 or localhost; anything else is refused.
- `compare` pairs files by name: `same`, `changed`, `added`, `removed` on stdout, and `index.html` with each pair side
  by side. Byte equality only; the eyes judge. No pixel-diff dependency.
- Exit: 0 done, 1 a shot failed, 2 usage, 3 no Playwright or browser, 4 the server never answered.
- Installed into `~/.agents/bin` with the lane tools.

## 8. Migrating a project that has a design lead

project-setup (setup and refresh) finds the old design lead by its committed additions file (native:
`.lane-control/additions/design-lead.md`; Cotal: `.lane-control/design-lead.md`), its settings (`design_lead`, the
`design-lead` pin), its rendered persona, its Orca worktree named `design-lead` and its terminal
`<project> design lead`. Like the move to `AGENTS.lane-control.md`, it changes nothing without the owner:
- In a terminal it shows the change and asks: the additions file's text goes into `DESIGN.md` (created from the
  template when missing) under `## From the design lead's additions`, the old file is removed in the same commit, and
  the next frontend lane folds that text into the sections. Nothing else is committed. On no, or without a terminal,
  it writes nothing and prints `design-lead: retired` with what it would do.
- The settings keys and pin are dropped from the settings store, said in the run's output.
- A running design-lead session and its worktree are never stopped or removed: setup names them, how many commits
  are unpushed and whether the tree is clean, and says to ask it for its handover and close it by hand. It never
  starts one again. `project-pause` names a leftover design lead and leaves it alone.
- The rendered `.lane-control/design-lead.md` is generated, and removed only when no design-lead session runs (that
  session's compaction hook reads it). Its git-exclude line stays, so it never shows as untracked.
- `--design-lead <harness/model>` is refused with a pointer to this spec (it asks for something that no longer
  exists); `--no-design-lead` is accepted and does nothing, so old command lines keep working.

## 9. The skills, on every harness and host

The owner, 2026-11-21: "We should allow opencode as well. We should make sure the skills are installed there too."
OpenCode lanes may do frontend and visual-direction work; `lane-tier` still decides which models run where.

Where the skills are found. Claude Code reads `~/.claude/skills` and its plugins. OpenCode v2 (2.2.1, checked
with a scratch HOME) reads `~/.config/opencode/skills`, `~/.claude/skills` (unless `OPENCODE_DISABLE_CLAUDE_CODE`
or `..._SKILLS` is set) and `~/.agents/skills`, plus the project's `.opencode`, `.claude` and `.agents`, and follows
symlinks. It does not read Claude's plugins. Here `frontend-design` and `ui-ux-pro-max` are Claude plugins, so
OpenCode lacks them; `modern-web-guidance` is in `~/.agents/skills`, so both have it. Boxes have whatever their owner
installed.

What project-setup does (native path, here and on a box, every setup and refresh, idempotent): for each skill in
`core/design-skills.tsv` (name, upstream git URL, pinned commit, path in the repo, licence, the Claude plugin that
also carries it):
- `~/.agents/skills/<name>` already there and not ours (the owner installed it): left alone, reported `exists`.
- Otherwise a sparse checkout of that path at that commit into `~/.agents/skill-sources/<name>@<commit12>/`, and
  `~/.agents/skills/<name>` a symlink into it (swapped by rename, older checkouts of ours removed). OpenCode reads it
  there; nothing goes into `~/.config/opencode/skills`.
- Claude gets a link `~/.claude/skills/<name>` to it only when its own plugin for that skill is not installed (no
  skill twice) and nothing is at that path.
- No git or no network: `design-skills missing <names>: <why>`, never a stop.
- The output names each skill and what happened (`created`, `updated`, `exists`, `missing`).

Sources, checked 2026-11-21; nothing of theirs is committed here, only the URL and commit:
| Skill | Upstream | Licence |
|---|---|---|
| `frontend-design` | anthropics/claude-plugins-official, `plugins/frontend-design/skills/frontend-design` | Apache-2.0 |
| `ui-ux-pro-max` | nextlevelbuilder/ui-ux-pro-max-skill, `.claude/skills/ui-ux-pro-max` | MIT |
| `modern-web-guidance` | GoogleChrome/modern-web-guidance, `skills/modern-web-guidance` | Apache-2.0 |

`ui-ux-pro-max` runs its search script as `${CLAUDE_PLUGIN_ROOT}/.claude/skills/ui-ux-pro-max/scripts/...`, which
is unset outside a Claude plugin. OpenCode prints each skill's base directory when it loads one, so the brief says:
where a skill names `${CLAUDE_PLUGIN_ROOT}/.claude/skills/<skill>/`, use that skill's base directory.
`modern-web-guidance` runs `npx modern-web-guidance`, so it needs Node and the network where the lane runs.

Still, without a skill: the brief and the design reviewer keep the floor and the tells in their own words, and the
report names what was missing; the coordinator does not give visual-direction work to a lane that lacks
`frontend-design` or `ui-ux-pro-max`.

## 10. Approaches

1. **Ephemeral agents plus a kit (chosen).** The method lives in the brief, the memory in `DESIGN.md`, the judgment
   in a cold reviewer; nothing long-lived to wake, pause or migrate across hosts.
2. **A design lead started per batch of frontend work.** Rejected: still a session with an address, a Run and
   messages, still stacks lanes on its branch, and brings back the lost-wake gap.
3. **The method in the reviewer only.** Rejected: review catches design late; the lane must plan before code.
4. **Vendor the skills into the repo.** Rejected: the owner asked not to; licences and drift.
5. **Link OpenCode to Claude's plugin folders.** Rejected: the paths carry a version that changes on update (dangling
   links), and a box without Claude has none. A pinned upstream checkout is the same on every machine.
6. **Install through each harness's own installer (`claude plugin install`, the `skills` CLI).** Rejected: two
   installers, unpinned versions, and OpenCode has no plugin installer for these.
7. **Only report which skills are found.** Rejected by the owner, 2026-11-21: OpenCode must have them too.
8. **lane-control ships a gallery package.** Rejected: projects differ; the framework's own tool or a plain route
   fits each better, and a package would be a dependency of every project.
9. **Screenshots through the Playwright MCP or the terminal browser.** Rejected as the base: not on every harness or
   box, not batchable. Agents may still use them to look around.
10. **Pixel diffs (pixelmatch, odiff).** Rejected for now: a dependency for little gain over side-by-side pairs.
11. **The reviewer builds the base itself in a throwaway worktree.** Rejected as the default: a full install per
   review. The lane shoots `before` first thing, in the same worktree the reviewer later reads.
12. **The design file in `.lane-control/`.** Rejected: it is the project's knowledge, not lane-control's.

## 11. Decisions (owner, 2026-11-21)

1. The design lead goes from the parked Orca + Cotal path too, mechanically (persona, adapt template, fixture, its
   spawn and verify); Cotal gets the migration notice but no design reviewer and no design skills install.
2. Every frontend lane gets the design reviewer.
3. `--design-lead` is refused; `--no-design-lead` is accepted and does nothing.
4. OpenCode lanes may do frontend work, and setup installs the skills for OpenCode too (section 9).

## 12. Tests (phase 2, test first)

Content tests: the brief addition, design reviewer, skill and template carry their rules; no persona, skill or doc
names the design lead except the migration. project-setup tests (stubbed Orca): the migration asks, moves and
commits on yes, writes nothing on no or without a terminal, never stops a session, drops the settings, refuses
`--design-lead`; the design reviewer is rendered; the skill and `design-shots` are installed; the design skills are installed from local
git repos standing in for upstream into a scratch HOME (never the real one): created, exists, updated, the owner's own
left alone, no Claude link when the plugin is installed, missing on a failed fetch. `design-shots` tests
with a stub Playwright and a local `python3 -m http.server`: the page x width x theme matrix, names, `shots.tsv`,
`--serve` cleanup, the refusals, `compare`. One real-browser test, skipped when no Playwright is found, run through
`lane-heavy`. Not tested: a real project migrated, a remote box.
