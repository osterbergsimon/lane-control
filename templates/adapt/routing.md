---
lane_control_template: 1
read_by: coordinator, design lead, reviewer
---
<!-- fixed -->
How lanes are routed in this project. Tier by the paths a task touches: the highest tier wins, in doubt one tier up.
The budget decides how many lanes run, never the tier. Deep whatever the paths: a diagnosis (cause unknown), a
cross-cutting refactor, a feature with subtle text or locale handling, an open design question, a follow-up of a
silent-loss or false-gate-claim finding. Swift only for prose docs, scripts without private data and CI; it may port
tests but never adds expectations, and never takes a task that needs private data. A PR touching a path above its
lane's tier fails review.

Review tiers, by what a PR touches, not by who wrote it. A: the coordinator's own checks only. B: plus an independent
review by a different model. C: plus a deep-tier review, and spec statements approved by the owner before merge.
<!-- /fixed -->

## Path map
<!-- slot path-map: required. Table `path glob | tier | review | why`. Last row is `**`. A file matching several rows takes the highest. Tiers: swift, standard, deep. Review: A, B, C. -->

## Deep whatever the paths, here
<!-- slot deep-extra: required. Kinds of task, beyond the list above, that are deep in this project, or "none". -->
