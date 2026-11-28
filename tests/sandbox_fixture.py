"""A scratch machine for the SBX-2 sandbox tests (lc-35qn): a temp HOME with fake floor files, a shared repo with an
origin, another lane's branch and the owner's unpushed commit, a lane worktree, the project's settings store, the
sandbox config and a current selftest, and `prepare` run as lane-control lane prepare --sandbox runs it (CI
answered read-only). Imported after testhome by tests/test_lane_broker.py, test_sandbox_shim.py,
test_sandbox_inside.py and test_sandbox_escapes.py."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "core"))
import extras as X  # noqa: E402
import sandbox as SB  # noqa: E402
import sandboxprep as SP  # noqa: E402

GIT_ID = ["-c", "user.name=t", "-c", "user.email=t@example.com"]
FLOOR = (".ssh/id_ed25519", ".config/lane-control/machine.conf.secret", ".local/share/opencode/auth.json",
         ".claude/.credentials.json", ".config/gh/hosts.yml", ".config/orca/orca-runtime.json", ".netrc",
         ".config/opencode/mcp-auth.json")


def git(*args, cwd, check=True):
    return subprocess.run(["git", *GIT_ID, *map(str, args)], cwd=cwd, capture_output=True, text=True, check=check,
                          stdin=subprocess.DEVNULL).stdout.strip()


class Fixture:
    def __init__(self, t: Path, mark: str = "FAKE-FLOOR-MARK", conf: "dict | None" = None, lane: str = "app-1"):
        self.t, self.mark, self.lane = t, mark, lane
        self.home = t / "home"
        self.home.mkdir(parents=True)
        self.env = {**{k: v for k, v in os.environ.items() if not k.startswith(("ORCA_", "LANE_", "XDG_"))},
                    "HOME": str(self.home), "XDG_CONFIG_HOME": str(self.home / ".config"),
                    "XDG_DATA_HOME": str(self.home / ".local/share"), "XDG_STATE_HOME": str(self.home / ".local/state"),
                    "XDG_CACHE_HOME": str(self.home / ".cache"), "GIT_CONFIG_NOSYSTEM": "1"}
        for k in ("GITHUB_TOKEN", "GH_TOKEN", "SSH_AUTH_SOCK", "LANE_MACHINE_CONF", "LANE_SANDBOX_CONF"):
            self.env.pop(k, None)
        for f in FLOOR:
            p = self.home / f
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(f"{mark} {f}\n")
        (self.home / ".gitconfig").write_text("[user]\n\tname = Owner\n\temail = owner@example.invalid\n")
        self.origin = t / "origin.git"
        self.repo = t / "code" / "app"
        self.repo.mkdir(parents=True)
        git("init", "-q", "--bare", "-b", "main", self.origin, cwd=t)
        git("init", "-q", "-b", "main", cwd=self.repo)
        (self.repo / "orca.yaml").write_text("base\n")
        (self.repo / "src.txt").write_text("src\n")
        git("add", ".", cwd=self.repo)
        git("commit", "-q", "-m", "base", cwd=self.repo)
        git("remote", "add", "origin", self.origin, cwd=self.repo)
        git("push", "-q", "origin", "main", cwd=self.repo)
        git("fetch", "-q", "origin", cwd=self.repo)
        self.base = git("rev-parse", "main", cwd=self.repo)
        (self.repo / "fixtures" / "customers").mkdir(parents=True)
        (self.repo / "fixtures" / "customers" / "a.csv").write_text(f"{mark} customer\n")
        (self.repo / ".env").write_text(f"KEY={mark}\n")
        git("switch", "-q", "-c", "lane/other", cwd=self.repo)
        (self.repo / "other.txt").write_text(f"{mark} other lane\n")
        git("add", "other.txt", cwd=self.repo)
        git("commit", "-q", "-m", "other", cwd=self.repo)
        self.other_sha = git("rev-parse", "HEAD", cwd=self.repo)
        git("switch", "-q", "main", cwd=self.repo)
        (self.repo / "wip.txt").write_text(f"{mark} unpushed\n")
        git("add", "wip.txt", cwd=self.repo)
        self.wip_sha = git("commit-tree", "-p", "HEAD", "-m", "wip", git("write-tree", cwd=self.repo), cwd=self.repo)
        git("rm", "-q", "--cached", "wip.txt", cwd=self.repo)
        (self.repo / "wip.txt").unlink()
        git("update-ref", "refs/owner/wip", self.wip_sha, cwd=self.repo)
        self.wt = t / "orca" / "workspaces" / "app" / "lane-1"
        self.wt.parent.mkdir(parents=True)
        git("worktree", "add", "-q", "-b", "lane-1", self.wt, "main", cwd=self.repo)
        self.conf = {"SANDBOX_BWRAP": "/nix/store/a-bubblewrap/bin/bwrap",
                     "SANDBOX_PASTA": "/nix/store/b-passt/bin/pasta",
                     "SANDBOX_NSENTER": "/nix/store/c-util-linux/bin/nsenter",
                     "SANDBOX_NFT": "/nix/store/d-nftables/bin/nft",
                     "SANDBOX_TOOL_PATH": ":".join(tool_dirs()), **(conf or {})}
        with mock.patch.dict(os.environ, self.env, clear=True):
            self.store = X.store_for(self.repo)
            self.store.mkdir(parents=True)
            (self.store / "settings.json").write_text(json.dumps(
                {"root": str(self.repo), "name": "app", "orchestrator": "native-orca",
                 "private": ["fixtures/customers"]}))
            cfg = self.home / ".config" / "lane-control"
            cfg.mkdir(parents=True, exist_ok=True)
            self.write_conf()
            (cfg / "machine.conf").write_text("SANDBOX_PROJECTS=app\n")
            self.selftest()
            (self.home / ".config" / "opencode").mkdir(parents=True, exist_ok=True)
            (self.home / ".config" / "opencode" / "opencode.json").write_text(json.dumps(
                {"model": "gw/chat", "provider": {"gw": {"npm": "@ai-sdk/openai-compatible", "options": {
                    "baseURL": "http://127.0.0.1:8317/v1", "apiKey": "{env:FAKE_GATEWAY_KEY}"}}}}))

    def write_conf(self):
        f = self.home / ".config" / "lane-control" / "sandbox.conf"
        f.write_text("".join(f"{k}={v}\n" for k, v in self.conf.items()))

    def selftest(self):
        f = SB.state_root() / "selftest.json"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps({"ok": True, "lane_control": SP.lc_version(),
                                 "pins": {k: self.conf[k] for k in SP.PIN_KEYS}}))

    def patched(self):
        """The environment and the CI answers prepare runs with."""
        stack = [mock.patch.dict(os.environ, self.env, clear=True),
                 mock.patch.object(SP, "github_slug", lambda root: "owner/app"),
                 mock.patch.object(SP, "gh_json", lambda path: {
                     "workflow": {"default_workflow_permissions": "read"}}.get(path.rsplit("/", 1)[-1],
                                                                              {"total_count": 0}))]
        return stack

    def prepare(self, role: str = "lanes", sha: "str | None" = None, **kw) -> dict:
        """Prepare and take the ticket (bound to a fake terminal); returns the taken ticket."""
        ps = self.patched()
        for p in ps:
            p.start()
        try:
            nonce = SP.prepare(self.repo, json.loads((self.store / "settings.json").read_text()), self.wt.resolve(),
                               role, self.lane, kw.pop("issue", "app-1x"), sha, say=lambda *_: None, **kw)
            return nonce
        finally:
            for p in reversed(ps):
                p.stop()

    def take(self, terminal: str = "term_test") -> dict:
        with mock.patch.dict(os.environ, self.env, clear=True):
            return SB.take_ticket(self.wt.resolve(), terminal)

    def clone(self) -> Path:
        with mock.patch.dict(os.environ, self.env, clear=True):
            return SB.lane_dir(self.lane) / "repo"


def tool_dirs() -> list:
    """Real store folders for git, bash, python3 and coreutils where the machine has them (the sandbox's tool path in
    the live tests); otherwise the folders of whatever is on PATH (the pure tests never run them)."""
    out = []
    for name in ("git", "bash", "python3", "env", "grep", "sed", "cat"):
        p = shutil.which(name)
        if p:
            d = str(Path(os.path.realpath(p)).parent)
            if d not in out:
                out.append(d)
    if not out or not all(d.startswith("/nix/store/") for d in out):
        return ["/nix/store/e-git/bin", "/nix/store/f-python3/bin"]  # not a nix machine: placeholders only
    return out
