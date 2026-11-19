# Switching a project to another orchestrator

The command changes files only: it strips the old orchestrator's instructions and config, sets up the new one's, on
a new branch in a new worktree, and opens one PR. Everything risky stays with the coordinator and the owner, in this
order. What carries over: beads issues and memories; the project's own rules (AGENTS.md minus orchestrator lines);
the latest handover; the role rules; budget and heavy-work limits; all work pushed to the remote.

1. **Check where the project is.** `scripts/orch status <project>`: which orchestrator it is on, and what of it is
   present. A project carrying two shows both.
2. **Save uncommitted work in every worktree.** For each worktree with changes, commit them and push to a branch
   `wip/<name>` (never stash, never force-push). Check with `git status` in each that nothing is left.
3. **Read the old handover and carry what matters.** `bd recall handover-latest`, plus the old orchestrator's dated
   handover if it wrote one. Keep what the next coordinator needs (open lanes, decisions waiting, the next step) in
   `handover-latest`; drop what only the old orchestrator needed.
4. **Run the switch.** `scripts/orch switch <project> --from <old> --to <new>` (add `--dry-run` first to see the
   plan). It opens one PR. Review it, then merge it on the owner's word.
5. **Stop the old coordinator.** Close its session and its workers' sessions the old orchestrator's way (its
   `setup.md` and `notes.md`); release kept-open terminals. Remove old worktrees only after step 2 is checked.
6. **Start a fresh one on the new orchestrator,** from the merged main, pointing it at AGENTS.md and
   `handover-latest` (the new orchestrator's `setup.md`).
7. **Confirm it has none of the old orchestrator's tools.** Ask the new coordinator which orchestration commands it
   has; none of the old orchestrator's may be among them (for example no `cotal_*` tools after leaving Orca + Cotal).
   Its first worker should report back the new way.
