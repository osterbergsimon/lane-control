Frontend: this lane changes what a user sees. These steps add to the ones above and never lower them.
F1. Before you plan, read `DESIGN.md` at the repo's root. If it is missing, create it from
    `~/.agents/skills/component-gallery/DESIGN.md`, filling what the code shows and nothing you guess. Read the
    `component-gallery` skill and the design skills `frontend-design`, `ui-ux-pro-max` and `modern-web-guidance`.
    Name any you lack in your report; never block on them. Where a skill names
    `${CLAUDE_PLUGIN_ROOT}/.claude/skills/<skill>/`, use that skill's base directory instead. Use `ui-ux-pro-max`
    to search, never with `--persist`: `DESIGN.md` is the project's one design memory.
F2. Shoot, before your first edit, the gallery (or the pages the issue touches) at the base with
    `~/.agents/bin/design-shots`, into `$(git rev-parse --git-dir)/design-shots/before` (the skill, section 5; start
    the server bound to 127.0.0.1).
F3. You plan before code. A new screen or a new visual direction gets a token plan in the PR body: 4-6 named
    colours with hex values, type roles and scale, spacing, the layout as an ASCII wireframe, 3-5 principles.
    Otherwise name the tokens and components you reuse. Then critique the plan against the issue, revise what reads as
    a default; the brief's own words always win over taste, and spend boldness in one place.
F4. The floor, always: it works at 390 px with no horizontal scroll and at 1280 px; visible keyboard focus; reduced
    motion respected; contrast at least 4.5:1 for text and 3:1 for UI parts; light and dark both right. None of the
    template tells unless the issue asks for one: cream + serif + terracotta, near-black + acid accent, identical
    rounded cards with the same shadow, ALL-CAPS eyebrows, middle-dot meta strings, an arrow on every link,
    fade-up on every section.
F5. Gallery: add or update an entry for each component you touch, every state, light and dark (the skill). No
    gallery in the project: report `gallery: none` and do not build one.
F6. Update `DESIGN.md` in the same PR when you change a token, a component or a rule: its section, and a dated line
    under Decisions.
F7. At your final commit shoot again into `.../design-shots/after`, run `~/.agents/bin/design-shots compare` on before
    and after, and look at every changed pair yourself before you report.
F8. Your report gets one more line, after `gates:`:
    ```
    design: DESIGN.md updated|unchanged · gallery +<n>|none · shots before+after|after|none · skills missing <names>|none
    ```
    A design reviewer grades your work cold as well as the code reviewer; fix its `blocker` and `major` findings
    like any review finding.
