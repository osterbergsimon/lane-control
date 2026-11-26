# Docs index

Every doc for the owner, one line each: what it is and when to read it. Paths are from this folder. New here? Start
with the first one.

## Start here

- [Getting started](getting-started.md): from an empty machine to a first merged lane, step by step. Read it first, and again when you set up a new machine.
- [How the parts fit](how-it-fits.md): one page on every moving part: who does what, a box and its two tunnels, the automations and budgets. Read it when you are unsure which piece does what.
- [The README](../README.md): what lane-control is, the layers, the doctrines and the map of files. Read it for the picture.

## Running projects

- [The ratchet (unattended improvement)](away-improve.md): lets coordinators keep improving a product on its own branch while you are away. Read it before you switch `lane-control ratchet on`.
- [Lane cleanup](orca-cleanup.md): how a merged lane's worktree is removed by Orca's own rule. Read it when setup says a cleanup rule is off.
- [Asks for Orca upstream](orca-upstream-asks.md): what we would ask Orca to change, with the evidence and what we do meanwhile; recorded, not filed. Read it before you file anything at Orca.
- [Issues doctrine](../core/issues.md): how beads issues are claimed, closed, parked and backed up. Read it when you move a project or restore beads.
- [Roles](../core/roles.md): the jobs and limits of the machine coordinator and a project coordinator. Read it to know who decides what.

## Machines and hosts

- [Machine notes](../core/machine.md): what is specific to a machine, and a checklist for a new box. Read it when you add a machine.
- [native Orca: setup](../orchestrators/native-orca/setup.md): installing and running native Orca, machine and project setup, and OpenCode on a self-hosted gateway. Read it when the guide says "see the setup".
- [native Orca on an SSH host](../orchestrators/native-orca/remote.md): setting up a project on a box and letting it run lanes on your desktop. Read it before you use `lane-control project setup <repo> <host>`.
- [The night manager](../orchestrators/native-orca/night-manager.md): what the hourly night automation does and what it will not do. Read it when you leave a machine on overnight.

## Models and tiers

- [Model routing](../core/model-routing.md): tiers, routing by paths, escalation, budget steps and limits. Read it to change which models run what.
- [OpenCode on a self-hosted gateway](../orchestrators/native-orca/setup.md#opencode-on-a-self-hosted-litellm-gateway): the config, `--suggest-tiers` and a machine without Claude. Read it before you use model aliases such as `chat` and `code`.

## Orchestrators

- [Orchestrators](../orchestrators/README.md): what an orchestrator is, the status table and how to add one. Read it to compare or add one.
- [Switching a project](../orchestrators/switching.md): moving a project from one orchestrator to another. Read it before you switch.
- [The trial checklist](../orchestrators/checklist.md): the one checklist for trying or retesting an orchestrator. Read it when you test one.
- [native Orca: record](../orchestrators/native-orca/record.md): status, decisions and history of the orchestrator in use.
- [Orca + Cotal: setup](../orchestrators/orca-cotal/setup.md): setting up the parked Orca + Cotal orchestrator. Read it only to retest it.
- [Rove: setup](../orchestrators/rove/setup.md): setting up the parked Rove orchestrator. Read it only to retest it.

## Reference and doctrine

- [Core](../core/README.md): what every orchestrator shares, the files in `core/`, one line on every script in the repo, and the paths kept for live machines. Read it to find a script.
- [Reliability log](../core/reliability.md): each failure of a coordinator, its durable fix and how to reuse it. Read it when something goes wrong twice.
- [Agent rules](../AGENTS.md): the checks and rules for agents that change this repo. Read it before you change anything here.

## Design specs, plans and research

- Specs ([docs/superpowers/specs](superpowers/specs)): why each feature is built the way it is.
- Plans ([docs/superpowers/plans](superpowers/plans)): how each feature was planned.
- Research ([docs/research](research)): what we learned about tools before choosing them.
- Each orchestrator's own `docs/` folder under `orchestrators/` holds its notes. Read them when you work on that orchestrator.
