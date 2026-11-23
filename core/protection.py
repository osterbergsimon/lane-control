"""Branch-protection report (project-setup; docs/away-improve.md). Reads how the project's integration branch is
protected on GitHub and says it plainly. It never changes a setting: every gh call is a read (`gh api <path>`, GET).

  everyone     protected for everyone: a pull request is required, admins are not exempt, no force pushes
  partly       protected, but something is missing (the report says what, e.g. admins exempt, so an agent running as an
               admin is not stopped)
  none         no protection on the branch
  ruleset      no classic protection, but a ruleset applies (which rules; who may bypass is not read here)
  unavailable  not available on the plan (HTTP 403, a private repo on a free plan)
  unknown      it could not be read (no gh, no GitHub remote, offline, no permission, an unclear answer)
"""
import json
import re
import subprocess
from pathlib import Path
from urllib.parse import quote

GH_TIMEOUT = 20


def _api(repo: Path, path: str) -> "tuple[int, str, str]":
    """(exit code, stdout, stderr) of a read-only `gh api <path>`; code -1 when gh could not run."""
    try:
        r = subprocess.run(["gh", "api", path], cwd=repo, capture_output=True, text=True, timeout=GH_TIMEOUT,
                           stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as e:
        return -1, "", str(e)
    return r.returncode, r.stdout, r.stderr.strip()


def _last_line(text: str) -> str:
    return (text.strip().splitlines() or ["no output"])[-1]


def _ci_exists(repo: Path) -> bool:
    wf = repo / ".github" / "workflows"
    return wf.is_dir() and any(p.suffix in (".yml", ".yaml") for p in wf.iterdir())


def classify(branch: str, body: dict, ci_exists: bool) -> "tuple[str, str]":
    """(what, detail) for a classic protection answer."""
    missing = []
    if not isinstance(body.get("required_pull_request_reviews"), dict):
        missing.append("no pull request is required (a push straight to it is allowed)")
    if not (body.get("enforce_admins") or {}).get("enabled"):
        missing.append("admins are exempt (so an agent running as an admin is not stopped)")
    if (body.get("allow_force_pushes") or {}).get("enabled"):
        missing.append("force pushes are allowed")
    notes = []
    reviews = body.get("required_pull_request_reviews")
    n = reviews.get("required_approving_review_count") if isinstance(reviews, dict) else 0
    if isinstance(n, int) and n > 0:
        notes.append(f"{n} approving review(s) are required, so a PR waits for a person")
    if (body.get("allow_deletions") or {}).get("enabled"):
        notes.append("the branch can be deleted")
    checks = body.get("required_status_checks")
    if isinstance(checks, dict):
        names = [c.get("context") for c in checks.get("checks") or [] if isinstance(c, dict) and c.get("context")] \
            or [c for c in checks.get("contexts") or [] if isinstance(c, str)]
        notes.append("CI required" + (f": {', '.join(names)}" if names else ""))
    elif ci_exists:
        notes.append("CI exists but is not required")
    tail = ("; " + "; ".join(notes)) if notes else ""
    if missing:
        return "partly", f"{branch}: " + "; ".join(missing) + tail
    return "protected", f"{branch}: for everyone (a pull request is required, admins included, no force pushes)" + tail


def report(repo: Path, branch: str) -> "tuple[str, str]":
    """(what, detail) for `branch` of the GitHub repo `repo` is a checkout of. Never raises."""
    base = f"repos/{{owner}}/{{repo}}/branches/{quote(branch, safe='/')}"
    code, out, err = _api(repo, base + "/protection")
    if code == 0:
        try:
            body = json.loads(out)
        except ValueError:
            body = None
        if not isinstance(body, dict):
            return "unknown", f"{branch}: gh gave an answer that is not protection settings"
        return classify(branch, body, _ci_exists(repo))
    if "HTTP 404" in err and "not protected" in err.lower():
        code, out, _ = _api(repo, f"repos/{{owner}}/{{repo}}/rules/branches/{quote(branch, safe='/')}")
        try:
            rules = json.loads(out) if code == 0 else []
        except ValueError:
            rules = []
        types = sorted({r.get("type") for r in rules if isinstance(r, dict) and r.get("type")}) if isinstance(rules, list) else []
        if types:
            return "ruleset", (f"{branch}: no classic branch protection, but a ruleset applies ({', '.join(types)}); who may "
                               "bypass it is not read here")
        return "none", f"{branch} has no protection: a push straight to it is allowed, for everyone"
    if "HTTP 403" in err and re.search(r"upgrade to github|make this repository public", err, re.I):
        return "unavailable", f"{branch}: not available on this repository's plan (HTTP 403; a private repo on a free plan has no protection)"
    return "unknown", f"{branch}: could not be read ({_last_line(err)})"
