#!/bin/sh
# Integration test for scripts/orca-host-install against a real SSH host (default: the trial box as user dev).
#   TEST_HOST=dev@trial-vps TEST_PORT=6769 PROXY_CONTAINER=agent-mux-trial-proxy-1 tests/test-orca-host-install.sh
# Destructive on the host: removes ~/.orca-remote and ~/.orca of the test user first.
set -u
here=$(cd "$(dirname "$0")/.." && pwd)
host=${TEST_HOST:-dev@trial-vps}
port=${TEST_PORT:-6769}
proxy=${PROXY_CONTAINER:-agent-mux-trial-proxy-1}
ver=0.1.0+b086a7d15d60
nodesha=e32e2d16081cf644f88392934288086e7662529d0d43277c34f7aad2207cfafb
fails=0
ok()   { echo "ok   $*"; }
bad()  { echo "FAIL $*"; fails=$((fails + 1)); }
check() { desc=$1; shift; if "$@"; then ok "$desc"; else bad "$desc"; fi; }
r() { ssh "$host" "$@"; }

r 'pkill -u "$(id -u)" -f "[o]rcad.js" ; rm -rf ~/.orca-remote ~/.orca' 2>/dev/null
mark=$(date -u +%s)

out1=$("$here/scripts/orca-host-install" "$host" --port "$port" 2>&1); rc1=$?
echo "$out1" | sed 's/^/  | /'
check "first run exits 0" [ "$rc1" -eq 0 ]
check "first run reports installed" sh -c 'echo "$1" | grep -q "^installed orcad-'"$ver"'"' _ "$out1"
check "slot complete" r "test -f ~/.orca-remote/orcad-$ver/.install-complete && test -f ~/.orca-remote/orcad-$ver/orcad.js"
check "node verified" r "test -f ~/.orca-remote/runtimes/node-$nodesha/.verified"
check "node version" sh -c '[ "$(ssh "$1" "~/.orca-remote/runtimes/node-$2/bin/node --version")" = v24.22.1 ]' _ "$host" "$nodesha"
check "remote dir is private" sh -c '[ "$(ssh "$1" "stat -c %a ~/.orca-remote")" = 700 ]' _ "$host"

out2=$("$here/scripts/orca-host-install" "$host" --port "$port" 2>&1)
check "second run reports exists" sh -c 'echo "$1" | grep -q "^exists orcad-'"$ver"'"' _ "$out2"

out3=$("$here/scripts/orca-host-install" "$host" --port "$port" --pair 2>&1); rc3=$?
echo "$out3" | sed 's/^/  | /'
check "pair exits 0" [ "$rc3" -eq 0 ]
check "pair reports paired" sh -c 'echo "$1" | grep -Eq "^(paired|already-paired) "' _ "$out3"
check "orcad answers on the host" r "ss -ltn | grep -q 127.0.0.1:$port"
envname=$(printf '%s' "$host" | tr -c 'A-Za-z0-9._-' '-')
orca=${ORCA_CLI:-$HOME/.config/orca/linux-orca-cli-shim/orca}
check "desktop reaches the paired runtime" sh -c '"$1" status --environment "$2" --json 2>/dev/null | grep -q "\"reachable\": true"' _ "$orca" "$envname"

out4=$("$here/scripts/orca-host-install" "$host" --port "$port" --pair 2>&1); rc4=$?
check "second pair exits 0" [ "$rc4" -eq 0 ]
check "second pair reports already-paired" sh -c 'echo "$1" | grep -q "^already-paired "' _ "$out4"

if docker inspect "$proxy" >/dev/null 2>&1; then
  hits=$(docker exec "$proxy" sh -c "awk -v m=$mark '\$1 >= m' /var/log/squid/access.log" | grep -cE 'nodejs\.org|onorca\.dev')
  check "no nodejs.org/onorca.dev egress" [ "$hits" -eq 0 ]
fi

# A foreign listener on the requested port is refused, not mistaken for orcad.
r "setsid nohup python3 -m http.server 6771 --bind 127.0.0.1 >/dev/null 2>&1 < /dev/null & sleep 1"
out5=$("$here/scripts/orca-host-install" "$host" --port 6771 --pair 2>&1); rc5=$?
r "pkill -u \$(id -u) -f '[h]ttp.server 6771'"
check "foreign listener refused" [ "$rc5" -ne 0 ]
check "foreign listener named" sh -c 'echo "$1" | grep -q "in use"' _ "$out5"

# An older orcad of ours on the port (Orca upgraded since): stopped and replaced by the current slot.
r "mkdir -p ~/tmp/old-orcad; printf 'import http.server,sys\nhttp.server.HTTPServer((\"127.0.0.1\",int(sys.argv[-1])),http.server.BaseHTTPRequestHandler).serve_forever()\n' > ~/tmp/old-orcad/orcad.js; cd ~/tmp/old-orcad; setsid nohup python3 orcad.js --json --bind 127.0.0.1 --port 6772 >/dev/null 2>&1 < /dev/null & sleep 1"
out6=$("$here/scripts/orca-host-install" "$host" --port 6772 --pair --no-tunnel 2>&1); rc6=$?
echo "$out6" | sed 's/^/  | /'
check "older orcad replaced" sh -c 'echo "$1" | grep -q "stopped older orcad 6772" && echo "$1" | grep -q "started orcad 6772"' _ "$out6"
check "replacement runs the current slot" r "pgrep -af 'orcad[.]js' | grep -F 'orcad-$ver/orcad.js' | grep -q -- '--port 6772'"
r "pkill -u \$(id -u) -f 'orcad[.]js .*--port 6772'" 2>/dev/null

check "refuses root without --allow-root" sh -c '! "$1/scripts/orca-host-install" "root@${2#*@}" --port "$3" >/dev/null 2>&1' _ "$here" "$host" "$port"

echo "$fails failure(s)"
[ "$fails" -eq 0 ]
