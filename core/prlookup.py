"""The one way a lane is mapped to its PR (lc-38y3): by branch name, then by the lane's HEAD commit.

The name misses a lane Orca recreated: its local branch gets a `-2` suffix and no upstream, so no PR has that head name
and a finished lane looks like it was never pushed. The fallback asks GitHub which PRs belong to HEAD
(`gh api repos/{owner}/{repo}/commits/<sha>/pulls`). That HEAD is a PR's head does not make the PR the lane's: a new
lane cut from another lane's branch, a fork's branch or a base branch sits at that PR's head too, and lane-sweep may
remove a lane over its PR. So a PR is taken only when its head commit IS HEAD, it comes from this repo (never a fork)
and its head branch is the lane's local branch without Orca's -<n> (`<user>/x` for `<user>/x-2`). Anything else is no
PR. A name is never matched loosely. Every gh call is a read, and "GitHub could not be asked" (CouldNotCheck) is never
the same answer as "no PR".

The branch name looked up first is the lane's upstream branch when the upstream is that same branch on a real remote
(`x` for a local `x` or `<user>/x`), else its local name; never a base branch it tracks.
"""
import json
import re
import subprocess

GH_TIMEOUT = 120
GIT_TIMEOUT = 60
UNKNOWN_COMMIT = "No commit found for SHA"  # GitHub's 422 for a commit it does not have: it is in no PR
RANK = {"open": 0, "merged": 1, "closed": 2}
ORCA_SUFFIX = re.compile(r"-[0-9]+$")  # what Orca adds to the local branch of a lane it recreates


class CouldNotCheck(Exception):
    """GitHub (or the lane's HEAD) could not be read, so whether there is a PR is not known."""


def none_found(branch: str, head: str) -> str:
    return f"no PR found for {branch or '(no branch)'} @{head[:7] or '?'}"


def _git(path, *args) -> "subprocess.CompletedProcess | None":
    try:
        return subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True, timeout=GIT_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired):
        return None


def _config(path, key: str) -> str:
    r = _git(path, "config", key)
    return r.stdout.strip() if r and r.returncode == 0 else ""


def head_of(path) -> str:
    """The lane's HEAD commit, or '' when git cannot say."""
    r = _git(path, "rev-parse", "--verify", "-q", "HEAD")
    return r.stdout.strip() if r and r.returncode == 0 else ""


def upstream(path, branch: str) -> "tuple[str, str]":
    """(remote, branch on the remote) to look the lane's PR and pushes up by: the upstream only when it is this branch
    on a real remote (a local upstream, remote ".", is another branch here, like a base branch)."""
    remote = _config(path, f"branch.{branch}.remote")
    merge = _config(path, f"branch.{branch}.merge").removeprefix("refs/heads/")
    if remote and remote != "." and merge in (branch, branch.rsplit("/", 1)[-1]):
        return remote, merge
    return (remote if remote and remote != "." else "origin"), branch


def _holds(repo, head: str, pr_head: str) -> bool:
    """HEAD is the PR's head or behind it."""
    if not (head and pr_head):
        return False
    if head == pr_head:
        return True
    r = _git(repo, "merge-base", "--is-ancestor", head, pr_head)
    return bool(r and r.returncode == 0)


def _gh_rows(repo, args: list, what: str, timeout: float, empty_when: str = "") -> list:
    try:
        r = subprocess.run(["gh", *args], cwd=repo, capture_output=True, text=True, timeout=timeout,
                           stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise CouldNotCheck(f"{what} failed: {type(e).__name__}") from None
    if r.returncode != 0:
        if empty_when and empty_when in r.stdout + r.stderr:
            return []
        why = (r.stderr.strip() or f"exit {r.returncode}").splitlines()[-1][:200]
        raise CouldNotCheck(f"{what} failed: {why}")
    try:
        rows = json.loads(r.stdout)
    except ValueError:
        rows = None
    if not isinstance(rows, list) or not all(isinstance(x, dict) for x in rows):
        raise CouldNotCheck(f"{what} printed no list of PRs")
    return rows


def _by_name(repo, name: str, timeout: float) -> list:
    """The PRs whose head branch is named `name`, newest first."""
    rows = _gh_rows(repo, ["pr", "list", "--head", name, "--state", "all", "--json", "number,headRefOid,url,state",
                           "--limit", "100"], f"gh pr list --head {name}", timeout)
    out = [{"number": x.get("number"), "state": str(x.get("state") or "").lower(),
            "headRefOid": x.get("headRefOid") or "", "url": x.get("url") or ""} for x in rows]
    return sorted((x for x in out if isinstance(x["number"], int)), key=lambda x: -x["number"])


def _by_head(repo, branch: str, head: str, timeout: float) -> "dict | None":
    """The PR whose head commit is `head` and that can be the lane's: from this repo (never a fork), its head branch
    named as the lane's local `branch` without Orca's -<n>, any `<user>/` prefix the same. An open one first, then a
    merged, then a closed one, each newest first."""
    name = ORCA_SUFFIX.sub("", branch)
    rows = _gh_rows(repo, ["api", f"repos/{{owner}}/{{repo}}/commits/{head}/pulls?per_page=100"],
                    f"gh api commits/{head[:7]}/pulls", timeout, empty_when=UNKNOWN_COMMIT)
    out = []
    for x in rows:
        h, b = x.get("head") or {}, x.get("base") or {}
        source = (h.get("repo") or {}).get("full_name")  # none: a deleted fork
        if not (source and source == (b.get("repo") or {}).get("full_name") and h.get("sha") == head
                and h.get("ref") == name and isinstance(x.get("number"), int)):
            continue
        state = "merged" if x.get("merged_at") else str(x.get("state") or "").lower()
        out.append({"number": x["number"], "state": state, "headRefOid": head, "url": x.get("html_url") or ""})
    return min(out, key=lambda x: (RANK.get(x["state"], len(RANK)), -x["number"]), default=None)


def find(repo, path, branch: str, head: str, timeout: float = GH_TIMEOUT) -> "dict | None":
    """{number, state (open, merged, closed), headRefOid, url} of the PR of the lane at `path` (local branch `branch`,
    HEAD `head`; gh runs in `repo`), or None when GitHub answered and there is none. In this order: the branch's newest
    merged PR that holds HEAD; its open PR; the lane's open or merged PR by head commit (_by_head); else what there
    is: a merged PR the branch moved on from (its headRefOid does not hold HEAD), then a closed one. CouldNotCheck when
    GitHub could not be asked, or HEAD is unreadable and the name shows nothing."""
    name = upstream(path, branch)[1] if branch else ""
    named = _by_name(repo, name, timeout) if name else []
    merged = next((x for x in named if x["state"] == "merged"), None)
    if merged and _holds(repo, head, merged["headRefOid"]):
        return merged
    opened = next((x for x in named if x["state"] == "open"), None)
    if opened:
        return opened
    older = merged or next(iter(named), None)
    if not head:
        if older:
            return older
        raise CouldNotCheck("the lane's HEAD cannot be read")
    headed = _by_head(repo, branch, head, timeout)
    if headed and headed["state"] != "closed":
        return headed
    return older or headed
