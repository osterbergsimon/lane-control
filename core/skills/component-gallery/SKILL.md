---
name: component-gallery
description: Build a project's component gallery the framework-native way (or a plain /gallery route), add an entry for each component a lane touches, and shoot it with design-shots for a before/after review. Use when a lane changes what a user sees, or an issue asks for a gallery.
---

# Component gallery

A gallery shows every component in every state, light and dark, on one place a browser can open. Lanes keep it
current; the design reviewer reads it and its shots. `DESIGN.md` at the project's root names where it is
(`DESIGN.md` template: `DESIGN.md` next to this file).

## 1. Use what the project has
- The project already has a gallery (Storybook, Histoire, Ladle, Lookbook, Widgetbook or the like): use it, its
  way. Add stories there, nothing beside it.
- Adding such a tool is a new dependency: only when the issue or the owner says so.
- No gallery and the issue does not ask for one: say `gallery: none` in your report and go on; never build a gallery
  unasked. Building one is its own issue.

## 2. Otherwise a plain route
When an issue asks for a gallery and the project has no tool for one: a route `/gallery` (or the framework's
equivalent: a page, a screen, a dev entry point), built in development only, never shipped.
- Every component in every state it has: default, hover, focus, active, disabled, loading, empty, error, long
  content (overflow, wrapping, the longest real value). Each state labelled in plain text.
- Real tokens and real components, imported, never copies.
- Use fixed data: no clocks, no random, no network; the same page every time.
- Show no motion that runs by itself; transitions only on interaction, and none under reduced motion.
- Light and dark: it follows `prefers-color-scheme` and also takes `?theme=light|dark`, so either can be forced.
- Hover and focus are shown with a forced state (a class or attribute the component already supports, or a wrapper
  that sets it), so a still screenshot shows them.
- A marker the page sets when it has rendered (for example `data-gallery-ready` on `body`), for `--wait-for`.

## 3. The pages file
`gallery.pages` at the root (or where `DESIGN.md` says): what `design-shots` shoots, one `[name] <path>` per line,
`#` comments. For Storybook, a path is a story's `iframe.html?id=<story-id>`.
```
buttons   /gallery#buttons
forms     /gallery/forms
```

## 4. When your lane touches a component
Do it in the same PR: add or update its gallery entry (every state, both themes), its line in the pages file when
it got a page of its own, and its line in `DESIGN.md`'s Components. A token or rule you changed: a dated line under
Decisions.

## 5. Shots
`design-shots` (in `~/.agents/bin`) runs headless Playwright, a fresh browser per shot, at 390 and 1280 px, light
and dark. Start the server bound to 127.0.0.1: `design-shots` only checks that the URL is loopback, and a
`--host` or `0.0.0.0` default opens the server to the network. The shots go under the worktree's git dir; never
commit the shots.
```
d="$(git rev-parse --git-dir)/design-shots/"
~/.agents/bin/design-shots shoot http://127.0.0.1:<port> --pages gallery.pages --out "$d/before" --serve "<start command>"
# ... your change ...
~/.agents/bin/design-shots shoot http://127.0.0.1:<port> --pages gallery.pages --out "$d/after" --serve "<start command>"
~/.agents/bin/design-shots compare "$d/before" "$d/after" --out "$d/compare"
```
Take `before` first thing, before your first edit. Open `compare/index.html` and look at every `changed` and
`added` pair yourself. Exit 3 means no Playwright or browser: it says what to install; say so in your report.
