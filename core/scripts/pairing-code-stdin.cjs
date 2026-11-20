// Preloaded into Orca's CLI (node --require) by project-setup on a box: reads the pairing code from stdin and puts it
// in place of the `-` after --pairing-code, inside the process only. The code never appears in the process's argv
// (ps, /proc/<pid>/cmdline), a file or a log. Orca 1.4.250's `environment add` reads the code only from argv.
"use strict";
function code() {
  const c = require("fs").readFileSync(0, "utf8").trim();
  if (!c) {
    process.stderr.write("pairing-code-stdin: no pairing code on stdin\n");
    process.exit(2);
  }
  return c;
}
const i = process.argv.indexOf("--pairing-code");
const j = process.argv.indexOf("--pairing-code=-");
if (i > 0 && process.argv[i + 1] === "-") {
  process.argv[i + 1] = code();
} else if (j > 0) {
  process.argv[j] = "--pairing-code=" + code();
}
