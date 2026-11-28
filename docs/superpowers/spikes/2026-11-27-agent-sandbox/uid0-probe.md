# uid 0 without capabilities (lc-9r81.2), SBX-2 task 1, 2026-11-28

Under the real chain (lane-sandbox-shim -> systemd-run scope -> pasta -> bwrap --cap-drop ALL, the mount plan, a
ticket from `prepare`), with a stub agent script, never a real OpenCode, Claude or gateway. bubblewrap 0.12.0, passt
2025_09_19, nodejs 24.21.0, git 2.54.0, python 3.12, nested in Orca's FHS environment.

| What | Result |
|---|---|
| `id` | `0:0 root` (pasta's user namespace maps the owner's uid to 0) |
| CapEff, NoNewPrivs | `0000000000000000`, `1` |
| `node --version`, `npm --version` | `v24.21.0`, `11.19.0`: both run |
| `npm config get prefix` | the node store path (read-only); a global `npm install` cannot write there |
| `git --version`, a commit, `git status` | run; commits carry the owner's name and email from the generated gitconfig |
| `python3` `os.getuid()` | `0` |
| `$HOME` writable | yes (a tmpfs) |
| `chown 1000 <file>` | `Invalid argument`: only uid 0 is mapped, so nothing can be given to another uid |
| `os.setuid(1000)` | `Invalid argument`: no way to become another uid |
| Node `os.userInfo()` | `{"uid": 0, "username": "root", "homedir": "/root"}` from `/etc/passwd`; `os.homedir()` follows `$HOME`, so tools that use it (OpenCode's config and data paths) get the tmpfs home. A tool that reads the passwd entry instead would look in `/root`, which does not exist inside |

Not measured: `opencode --version` and `opencode models` (no real OpenCode is run before the owner's go), Playwright's
headless browser (it downloads a browser; a lane project that needs it gets it in its tool list, task 9), and pasta
attaching to bwrap's own user namespace outside Orca's FHS environment and on a remote box (this session runs inside
the FHS environment; no box was used). Approach A2' (uid 1000 inside) stays open until that is measured.

Findings for the spec: tools that refuse to run as root (some package managers' "running as root" checks, Claude's
`--dangerously-skip-permissions`, phase 3) may refuse here; a lane whose tool does is the first thing task 9 records.
