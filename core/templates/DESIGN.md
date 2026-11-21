# Design

The project's design memory: every frontend lane reads it first and updates it in the same PR when it changes a
token, a component or a rule. Keep it under about 150 lines; link to token files rather than copying them. Where
something is not known yet, write `<!-- unknown -->`, never a guess.

## Subject
<!-- Who uses it, for what, and the one primary job of the main screen. -->

## Where it lives
<!-- Frontend paths and stack. The gallery: the tool or route, and its pages file (gallery.pages). -->

## Tokens
<!-- 4-6 named colours with hex values, light and dark; or the token file's path. -->

## Type
<!-- Families, their roles, the scale. -->

## Spacing and layout
<!-- Spacing scale, breakpoints, grid; radius and shadow rules (what earns which). -->

## Components
<!-- One line each: name, path, gallery entry, the states it shows. -->

## Principles
<!-- 3-5, this project's own: what makes it this product and not a template. -->

## Quality floor, added
<!-- What this product adds to the brief's floor (widths, locales, devices, input methods), or "none". -->

## Seeing it run
<!-- Start command on 127.0.0.1, how to get past login with a stub, what must be stubbed, and the exact line, e.g.
design-shots shoot http://127.0.0.1:<port> --pages gallery.pages --out "$(git rev-parse --git-dir)/design-shots/after"
It shoots 390 and 1280 px, light and dark. -->

## Decisions
<!-- Dated one-liners, newest last: what changed and why. -->
