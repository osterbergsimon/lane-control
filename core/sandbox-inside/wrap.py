#!/usr/bin/env python3
"""The commands a sandboxed lane has inside (SBX-2 task 6, lc-35qn): orca, orca-ide, gh, git, bd and lane-heavy, all
links to this file in core/sandbox-inside/bin (first on PATH inside, and at ~/.agents/bin where personas name them).
Each turns the few forms a lane needs into one typed request to the broker on $LANE_BROKER (/run/lane/broker.sock);
everything else is refused with one line. Nothing here can do more than the broker allows: it runs inside.

  git push [-u|--set-upstream] [origin] [<branch>]   bundle -> /run/lane/outbox/push.bundle, `push`; on success the
                                                      tracking ref origin/<branch> (and with -u the upstream)
  git fetch [origin] | git pull [--no-rebase] [origin]  `fetch`, then from the inbox bundle (pull merges; --rebase is
                                                      refused: lanes merge, pushes are fast-forward)
  git <anything else>                                 the real git
  orca|orca-ide orchestration send --type worker_done|escalation ...  `report` / `escalate`
  orca|orca-ide orchestration ask --question Q | check | reply --id I --body B   `ask`, `check`, `reply`
  gh pr create|view|checks|diff|comment|edit ...      the lane branch's own PR
  gh run view <id> [--log-failed]                     `run-view`
  bd show <id> | update <id> --append-notes T | update <id> --claim | create --title T --description D [-t] [-p]
  lane-heavy <command...>                             holds a `heavy-slot` while the command runs
  lane-enter <agent> [args]                           the chain's first step: checks out a clone made --no-checkout
                                                      (no index yet), then execs the agent"""
import json
import os
import socket
import subprocess
import sys
from pathlib import Path

ME = Path(__file__).resolve()
BIN = ME.parent / "bin"


def die(msg: str, code: int = 1):
    print(f"{NAME}: {msg}", file=sys.stderr)
    sys.exit(code)


def broker(req: dict, hold: bool = False):
    path = os.environ.get("LANE_BROKER") or "/run/lane/broker.sock"
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        s.connect(path)
    except OSError as e:
        die(f"the broker is not reachable ({e.strerror}); this command works only through it")
    s.sendall(json.dumps(req).encode() + b"\n")
    if hold:
        line = b""
        while not line.endswith(b"\n"):
            chunk = s.recv(4096)
            if not chunk:
                break
            line += chunk
        return json.loads(line or b"{}"), s
    s.shutdown(socket.SHUT_WR)
    data = b""
    while chunk := s.recv(65536):
        data += chunk
    s.close()
    try:
        return json.loads(data)
    except ValueError:
        die("the broker gave no answer")


def done(out: dict) -> int:
    if not out.get("ok"):
        die(f"refused: {out.get('error', 'no reason given')}")
    text = out.get("output")
    if text:
        sys.stdout.write(text if text.endswith("\n") else text + "\n")
    return 0


def opts(args: list, flags: dict) -> tuple:
    """--name value | --name=value | -x value for the names in flags ({flag: key}); a bare flag in flags with key
    ending in '!' is a switch. Returns (values, positionals); an unknown flag refuses."""
    vals, pos, i = {}, [], 0
    expanded = []
    for a in args:  # review r1 n1: bundled short switches (-qu) are split when each one is a known switch
        if len(a) > 2 and a[0] == "-" and a[1] != "-" and "=" not in a and \
                all(flags.get(f"-{c}", "").endswith("!") for c in a[1:]):
            expanded += [f"-{c}" for c in a[1:]]
        else:
            expanded.append(a)
    args = expanded
    while i < len(args):
        a = args[i]
        if a.startswith("-") and a != "-":
            name, eq, val = a.partition("=")
            if name not in flags:
                die(f"{name} is not available inside the sandbox")
            key = flags[name]
            if key.endswith("!"):
                vals[key[:-1]] = True
            else:
                if not eq:
                    i += 1
                    if i >= len(args):
                        die(f"{name} needs a value")
                    val = args[i]
                vals[key] = val
        else:
            pos.append(a)
        i += 1
    return vals, pos


def body_of(v: dict) -> "str | None":
    if "body_file" in v:
        f = v["body_file"]
        return sys.stdin.read() if f == "-" else Path(f).read_text()
    return v.get("body")


# --- git -----------------------------------------------------------------------------------------------------------

def real_git() -> str:
    for d in os.environ.get("PATH", "").split(":"):
        f = Path(d or ".") / "git"
        try:
            if f.is_file() and os.access(f, os.X_OK) and f.resolve() != ME and Path(d).resolve() != BIN.resolve():
                return str(f)
        except OSError:
            continue
    die("no real git on PATH")


def g(*args, check=True, capture=True) -> str:
    r = subprocess.run([real_git(), *args], capture_output=capture, text=True)
    if check and r.returncode != 0:
        die(f"git {args[0]}: {(r.stderr or '').strip().splitlines()[-1:] or ['failed']}")
    return (r.stdout or "").strip()


def git_main(args: list) -> int:
    i = 0
    while i < len(args) and args[i].startswith("-"):  # global options: -C <dir>, -c k=v, --no-pager ...
        i += 2 if args[i] in ("-C", "-c") else 1
    sub, rest = (args[i], args[i + 1:]) if i < len(args) else (None, [])
    if sub == "push":
        return git_push(args[:i], rest)
    if sub in ("fetch", "pull"):
        return git_fetch(args[:i], sub, rest)
    os.execv(real_git(), ["git", *args])


def git_push(glob: list, rest: list) -> int:
    if glob:
        die("push with global options is not available inside the sandbox")
    v, pos = opts(rest, {"-u": "u!", "--set-upstream": "u!", "-q": "q!", "--quiet": "q!"})
    if pos[:1] and pos[0] != "origin":
        die("push goes to origin only (the broker pushes the lane's own branch)")
    branch = g("symbolic-ref", "--short", "HEAD")
    for p in pos[1:]:
        if p not in (branch, "HEAD", f"{branch}:{branch}", f"refs/heads/{branch}"):
            die(f"push only the lane's branch ({branch}): no other ref, no force, no delete")
    outbox = Path(os.environ.get("LANE_OUTBOX") or "/run/lane/outbox")
    bundle = outbox / "push.bundle"
    try:
        bundle.unlink()
    except FileNotFoundError:
        pass
    ahead = g("rev-list", "--count", f"refs/heads/{branch}", "--not", "--remotes=origin")
    if ahead == "0":
        print("Everything up-to-date")
        return 0
    g("bundle", "create", "-q", str(bundle), f"refs/heads/{branch}", "--not", "--remotes=origin")
    out = broker({"request": "push"})
    if not out.get("ok"):
        die(f"push refused: {out.get('error')}")
    sha = out.get("sha") or g("rev-parse", f"refs/heads/{branch}")
    g("update-ref", f"refs/remotes/origin/{branch}", sha)
    if v.get("u"):
        g("config", f"branch.{branch}.remote", "origin")
        g("config", f"branch.{branch}.merge", f"refs/heads/{branch}")
    if not v.get("q"):
        print(out.get("output") or f"pushed {sha[:12]}")
    return 0


def git_fetch(glob: list, sub: str, rest: list) -> int:
    if glob:
        die(f"{sub} with global options is not available inside the sandbox")
    flags = {"-q": "q!", "--quiet": "q!", "--no-rebase": "norebase!", "--ff": "ff!", "--no-edit": "noedit!",
             "--rebase": "rebase!", "-r": "rebase!", "--prune": "prune!", "--all": "all!", "--no-tags": "nt!"}
    v, pos = opts(rest, flags)
    if v.get("rebase"):
        die("pull --rebase is refused: lanes merge the base (pushes are fast-forward only)")
    if pos[:1] and pos[0] != "origin":
        die(f"{sub} only from origin (the base, through the broker)")
    out = broker({"request": "fetch"})
    if not out.get("ok"):
        die(f"fetch refused: {out.get('error')}")
    if out.get("new"):
        ref = out["ref"]
        g("fetch", "-q", "--no-tags", out["bundle"], f"{ref}:{ref}")
        if not v.get("q"):
            print(f"fetched {out['new']} new base commit(s) to {ref.replace('refs/remotes/', '')}")
    elif not v.get("q"):
        print("Already up to date with the base.")
    if sub == "pull":
        base = (out.get("ref") or "").replace("refs/remotes/", "") or None
        if not base:
            up = g("for-each-ref", "--format=%(refname:short)", "refs/remotes/origin/", check=False).split()
            base = next((x for x in up if x != f"origin/{g('symbolic-ref', '--short', 'HEAD')}"), None)
        if base:
            r = subprocess.run([real_git(), "merge", "--no-edit", base])
            return r.returncode
    return 0


# --- orca ----------------------------------------------------------------------------------------------------------

def orca_main(args: list) -> int:
    if args[:1] != ["orchestration"] or len(args) < 2:
        die("only `orchestration send|ask|check|reply` are available inside the sandbox")
    verb, rest = args[1], args[2:]
    common = {"--from": "from", "--json": "json!", "--terminal": "terminal"}
    if verb == "send":
        v, pos = opts(rest, {**common, "--type": "type", "--subject": "subject", "--body": "body",
                             "--body-file": "body_file", "--task-id": "task", "--dispatch-id": "dispatch",
                             "--outcome": "outcome", "--files-modified": "files"})
        body = body_of(v)
        t = v.get("type")
        if pos or t not in ("worker_done", "escalation"):
            die("send: only --type worker_done or escalation, to your coordinator")
        req = {"request": "report" if t == "worker_done" else "escalate", "task": v.get("task", ""),
               "dispatch": v.get("dispatch", ""), "subject": v.get("subject", ""), "body": body or ""}
        if t == "worker_done":
            req["outcome"] = v.get("outcome", "")
            if v.get("files"):
                req["files"] = [x for x in v["files"].split(",") if x]
        return done(broker(req))
    if verb == "ask":
        v, pos = opts(rest, {**common, "--question": "question"})
        return done(broker({"request": "ask", "question": v.get("question", "")}))
    if verb == "check":
        opts(rest, {**common, "--unread": "u!", "--all": "a!", "--peek": "p!"})
        return done(broker({"request": "check"}))
    if verb == "reply":
        v, pos = opts(rest, {**common, "--id": "id", "--body": "body", "--body-file": "body_file"})
        return done(broker({"request": "reply", "id": v.get("id", ""), "body": body_of(v) or ""}))
    die(f"orchestration {verb} is not available inside the sandbox")


# --- gh ------------------------------------------------------------------------------------------------------------

def gh_main(args: list) -> int:
    if args[:2] == ["run", "view"]:
        v, pos = opts(args[2:], {"--log-failed": "lf!", "--log": "lf!"})
        if len(pos) != 1:
            die("run view <run id>")
        return done(broker({"request": "run-view", "run": pos[0]}))
    if args[:1] != ["pr"] or len(args) < 2:
        die("only `gh pr create|view|checks|diff|comment|edit` and `gh run view` are available inside")
    verb, rest = args[1], args[2:]
    if verb == "create":
        v, pos = opts(rest, {"--title": "title", "-t": "title", "--body": "body", "-b": "body",
                             "--body-file": "body_file", "-F": "body_file", "--base": "base", "--head": "head"})
        return done(broker({"request": "pr-create", "title": v.get("title", ""), "body": body_of(v) or ""}))
    if verb in ("view", "checks", "diff"):
        opts(rest, {"--json": "json", "-q": "q", "--jq": "q", "--watch": "w!", "--color": "c"})
        return done(broker({"request": f"pr-{verb}"}))
    if verb == "comment":
        v, pos = opts(rest, {"--body": "body", "-b": "body", "--body-file": "body_file", "-F": "body_file"})
        return done(broker({"request": "pr-comment", "body": body_of(v) or ""}))
    if verb == "edit":
        v, pos = opts(rest, {"--title": "title", "-t": "title", "--body": "body", "-b": "body",
                             "--body-file": "body_file", "-F": "body_file"})
        req = {"request": "pr-edit"}
        if "title" in v:
            req["title"] = v["title"]
        b = body_of(v)
        if b is not None:
            req["body"] = b
        return done(broker(req))
    die(f"gh pr {verb} is not available inside the sandbox")


# --- bd ------------------------------------------------------------------------------------------------------------

def bd_main(args: list) -> int:
    if args[:1] == ["show"]:
        v, pos = opts(args[1:], {"--json": "j!", "--id": "id"})
        ids = pos + ([v["id"]] if "id" in v else [])
        if len(ids) != 1:
            die("show <one id>")
        return done(broker({"request": "bd-show", "id": ids[0]}))
    if args[:1] == ["update"]:
        v, pos = opts(args[1:], {"--append-notes": "text", "--notes": "text", "--claim": "claim!"})
        if v.get("claim"):
            return done(broker({"request": "bd-claim"}))
        if "text" in v:
            return done(broker({"request": "bd-note", "text": v["text"]}))
        die("update: --claim or --append-notes on your own issue only")
    if args[:1] == ["create"]:
        v, pos = opts(args[1:], {"--title": "title", "--description": "description", "-d": "description",
                                 "--type": "type", "-t": "type", "--priority": "priority", "-p": "priority"})
        title = v.get("title") or (pos[0] if pos else "")
        req = {"request": "bd-create", "title": title, "description": v.get("description", "")}
        for k in ("type", "priority"):
            if k in v:
                req[k] = v[k].lstrip("Pp") if k == "priority" else v[k]
        return done(broker(req))
    die("only `bd show`, `bd update --claim|--append-notes` and `bd create` are available inside the sandbox")


# --- lane-heavy and the entry --------------------------------------------------------------------------------------

def heavy_main(args: list) -> int:
    if not args:
        die("lane-heavy <command...>")
    if os.environ.get("LANE_HEAVY_HOLDS_SLOT"):
        os.execvp(args[0], args)
    out, s = broker({"request": "heavy-slot"}, hold=True)
    if not out.get("ok"):
        die(f"no slot: {out.get('error')}")
    try:
        return subprocess.run(args, env={**os.environ, "LANE_HEAVY_HOLDS_SLOT": "1"}).returncode
    finally:
        s.close()


def released() -> bool:
    """The launcher writes its release token to the inbox (read-only here) only after the network filter and the run
    record: bwrap also goes on when the launcher dies, so the block fd alone proves nothing (review r1 M2)."""
    tok = os.environ.get("LANE_RELEASE") or ""
    f = Path(os.environ.get("LANE_INBOX") or "/run/lane/inbox") / "release"
    try:
        fd = os.open(f, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd) as fh:
            return bool(tok) and fh.read(200).strip() == tok
    except OSError:
        return False


def enter_main(args: list) -> int:
    if not args:
        die("lane-enter <agent> [args]")
    if not released():
        die("the launcher did not release this start (it ended before the network filter was set): nothing runs", 3)
    name = None
    if args[:1] == ["--argv0"] and len(args) > 2:
        name, args = args[1], args[2:]
    if Path(".git").is_dir() and not Path(".git/index").exists():
        r = subprocess.run([real_git(), "reset", "-q", "--hard", "HEAD"])
        if r.returncode != 0:
            die("could not check out the clone's files")
    try:
        os.execvp(args[0], [name or args[0], *args[1:]])
    except OSError as e:
        die(f"the agent {args[0]} cannot be started inside ({e.strerror}): nothing runs", 3)


NAME = Path(sys.argv[0]).name
MAIN = {"git": git_main, "orca": orca_main, "orca-ide": orca_main, "gh": gh_main, "bd": bd_main,
        "lane-heavy": heavy_main, "lane-enter": enter_main}

if __name__ == "__main__":
    if NAME == "wrap.py" and sys.argv[1:2] and sys.argv[1] in MAIN:
        NAME = sys.argv[1]
        sys.exit(MAIN[NAME](sys.argv[2:]))
    if NAME not in MAIN:
        die(__doc__, 2)
    sys.exit(MAIN[NAME](sys.argv[1:]) or 0)
