# CI probe (lc-9r81.3), SBX-2 task 1, 2026-11-28

The owner's decision 2 (2026-11-28): a sandboxed project's CI must have a read-only default workflow token, no
secrets and no self-hosted runners; `lane-control machine check --sandbox` reports it per project in
SANDBOX_PROJECTS and `lane-control lane prepare --sandbox` refuses a project that fails it
(`core/sandboxprep.py` `ci_check`).

Read with `gh api` (GET only) for this repository, the one sandbox candidate named in public:

| Setting | Value |
|---|---|
| default workflow token (`actions/permissions/workflow`) | `read`; may not approve pull request reviews |
| Actions secrets | 0 |
| organization secrets | not applicable (a user's repository: the endpoint answers "Validation Failed", so the check asks for them only when the owner is an organization) |
| environments (and their secrets) | 0 |
| self-hosted runners | 0 |
| workflows | `test.yml`: `pull_request` and `push` to `main`, `permissions: contents: read`, `ubuntu-latest` |

What a sandboxed lane's branch and PR run: `test.yml` from the branch itself, so the branch's own test suite and its
own copy of the workflow file, with a read-only `GITHUB_TOKEN`, no secrets, on a GitHub-hosted runner. A push that
changes any CI file is refused by the broker unless the owner runs `lane-control lane approve-ci <lane> <sha>`, so
the workflow file a lane's PR runs is `main`'s unless the owner let that change through. What stays: an existing
workflow runs the lane's code (its tests) before review, with that read-only token; a lane can make CI fail or use
the runner's minutes, and can send data from the runner to the internet, which it can do from the sandbox anyway.

Other projects the owner may sandbox are checked by the same code when they are added to SANDBOX_PROJECTS; their
settings are not written into this public repository.
