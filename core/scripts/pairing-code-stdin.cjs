// Preloaded into Orca's CLI (node --require) by project-setup on a box: reads the pairing code from stdin and puts it
// in place of the `-` after --pairing-code, inside the process only. The code never appears in the process's argv
// (ps, /proc/<pid>/cmdline), a file or a log. Orca 1.4.250's `environment add` reads the code only from argv.
"use strict";
const i = process.argv.indexOf("--pairing-code");
if (i > 0 && process.argv[i + 1] === "-") {
  const code = require("fs").readFileSync(0, "utf8").trim();
  if (!code) {
    process.stderr.write("pairing-code-stdin: no pairing code on stdin\n");
    process.exit(2);
  }
  process.argv[i + 1] = code;
}
