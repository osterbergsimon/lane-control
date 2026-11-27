"""Helpers for spike.sh (SBX-1). Run with `python3 -I`. Each subcommand prints one result and exits 0 or 1.

  safe-copy SRC DST CAP        the broker's read of the lane's outbox: no-follow, non-blocking, regular file, size cap
  history-hits REPO REV... -- PRIVATE...   paths in every delivered commit (all depths, merges, side branches)
                               that are --private paths or secret env files; exit 1 when any
  ci-hits REPO REV...          paths under CI configuration in the given commits; exit 1 when any
  host-addrs IP_BIN OUT        the host's non-loopback addresses, one per line, into OUT (never printed)
  nft-rules ADDRS OUT          the namespace's output filter: host addresses, link-local, private ranges dropped
  host-pid MNTNS NSPID         the host pid of the sandbox process in that mount namespace with that pasta-level pid
  cmdline-scan NEEDLE_FILE     how many host processes show the needle in their (world-readable) argv
  serve OPEN CLOSED UDP HOSTPORT ABSTRACT   listeners for the network checks (runs until killed)
  inbound PORT ADDRS           how many of 127.0.0.1 and the host addresses reach a port opened in the sandbox
"""
import errno
import json
import os
import socket
import stat
import subprocess
import sys
import time

TEMPLATES = {".env.example", ".env.sample", ".env.template", ".env.dist"}
CI_PREFIXES = (".github/workflows/", ".github/actions/", ".circleci/", ".buildkite/", ".gitlab/")
CI_FILES = {".gitlab-ci.yml", "azure-pipelines.yml", "jenkinsfile", ".travis.yml", "bitbucket-pipelines.yml"}


def norm(p: str) -> str:
    p = p.strip().lower()
    while p.startswith("./"):
        p = p[2:]
    return p.rstrip("/")


def changed_paths(repo, revs):
    """Every path any delivered commit adds or changes, against each parent (-m), the root included: a file added and
    removed on a side branch that was merged still shows in that branch's commits."""
    commits = subprocess.run(["git", "-C", repo, "rev-list", *revs], check=True, capture_output=True,
                             text=True).stdout
    out = subprocess.run(["git", "-C", repo, "diff-tree", "--stdin", "-r", "-m", "--root", "--no-commit-id",
                          "--name-only", "--no-renames"], input=commits, check=True, capture_output=True,
                         text=True).stdout
    return {line for line in out.splitlines() if line}


def history_hits(repo, revs, private):
    priv = [norm(p) for p in private if norm(p)]
    hits = []
    for path in sorted(changed_paths(repo, revs)):
        low = path.lower()
        base = low.rsplit("/", 1)[-1]
        if any(low == p or low.startswith(p + "/") for p in priv):
            hits.append(path)
        elif (base == ".env" or base.startswith(".env.")) and base not in TEMPLATES:
            hits.append(path)
    return hits


def main(argv):
    cmd = argv[0]
    if cmd == "safe-copy":
        src, dst, cap = argv[1], argv[2], int(argv[3])
        try:
            fd = os.open(src, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        except OSError as e:
            print(f"refused: {errno.errorcode.get(e.errno, e.errno)} (a link, or missing)"); return 1
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode):
                print("refused: not a regular file"); return 1
            if st.st_size > cap:
                print(f"refused: {st.st_size} bytes, over the cap"); return 1
            with open(dst, "wb") as out:
                left = cap + 1
                while chunk := os.read(fd, 1 << 20):
                    left -= len(chunk)
                    if left <= 0:
                        print("refused: grew past the cap"); return 1
                    out.write(chunk)
        finally:
            os.close(fd)
        print("copied"); return 0
    if cmd == "history-hits":
        i = argv.index("--")
        hits = history_hits(argv[1], argv[2:i], argv[i + 1:])
        print("hits: " + (" ".join(hits) if hits else "none")); return 1 if hits else 0
    if cmd == "ci-hits":
        hits = [p for p in sorted(changed_paths(argv[1], argv[2:]))
                if p.lower().startswith(CI_PREFIXES) or p.lower().rsplit("/", 1)[-1] in CI_FILES]
        print("ci: " + (" ".join(hits) if hits else "none")); return 1 if hits else 0
    if cmd == "host-addrs":
        data = json.loads(subprocess.run([argv[1], "-j", "addr"], check=True, capture_output=True, text=True).stdout)
        addrs = [a["local"] for i in data for a in i.get("addr_info", [])
                 if a.get("scope") != "host" and not a["local"].startswith(("127.", "::1"))]
        with open(argv[2], "w") as f:
            f.write("".join(a + "\n" for a in addrs))
        print(f"{len(addrs)} host addresses"); return 0
    if cmd == "nft-rules":
        addrs = [a for a in open(argv[1]).read().split() if a]
        v4 = [a for a in addrs if ":" not in a]
        v6 = [a for a in addrs if ":" in a and not a.lower().startswith("fe80")]
        lines = ["table inet sbx {", "  chain out {", "    type filter hook output priority 0; policy accept;",
                 "    ip daddr 169.254.1.53 accept"]
        if v4:
            lines.append("    ip daddr { " + ", ".join(v4) + " } drop")
        if v6:
            lines.append("    ip6 daddr { " + ", ".join(v6) + " } drop")
        lines += ["    ip daddr { 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 169.254.0.0/16, 100.64.0.0/10 } drop",
                  "    ip6 daddr { fc00::/7, fe80::/10 } drop", "  }", "}"]
        with open(argv[2], "w") as f:
            f.write("\n".join(lines) + "\n")
        print("rules written"); return 0
    if cmd == "host-pid":
        want, nspid = f"mnt:[{argv[1]}]", argv[2]
        for p in os.listdir("/proc"):
            if not p.isdigit():
                continue
            try:
                if os.readlink(f"/proc/{p}/ns/mnt") != want:
                    continue
                for line in open(f"/proc/{p}/status"):
                    if line.startswith("NSpid:") and nspid in line.split()[2:3]:
                        print(p); return 0
            except OSError:
                continue
        print("none"); return 1
    if cmd == "cmdline-scan":
        needle = open(argv[1], "rb").read().strip()
        n = 0
        for p in os.listdir("/proc"):
            if p.isdigit():
                try:
                    n += needle in open(f"/proc/{p}/cmdline", "rb").read()
                except OSError:
                    pass
        print(n); return 0
    if cmd == "serve":
        op, cp, up, hp, name = int(argv[1]), int(argv[2]), int(argv[3]), int(argv[4]), argv[5]
        socks = []
        for host, p in (("127.0.0.1", op), ("127.0.0.1", cp), ("0.0.0.0", hp)):
            s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); s.bind((host, p)); s.listen(8)
            socks.append(s)
        u = socket.socket(socket.AF_UNIX); u.bind("\0" + name); u.listen(8)
        d = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); d.bind(("127.0.0.1", up))
        d.settimeout(1)
        end = time.time() + 900
        while time.time() < end:
            try:
                data, peer = d.recvfrom(100); d.sendto(b"pong", peer)
            except OSError:
                pass
        return 0
    if cmd == "inbound":
        port, addrs = int(argv[1]), ["127.0.0.1"] + [a for a in open(argv[2]).read().split() if a]
        n = 0
        for a in addrs:
            try:
                socket.create_connection((a, port), timeout=1).close(); n += 1
            except OSError:
                pass
        print(n); return 0
    print("unknown command"); return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
