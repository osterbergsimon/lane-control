// Mint a wsp pairing code that pairs a browser as the OWNER (see docs/wsp.md, section 4).
//
// `wsp host pair` mints device codes; in wsp 0.3.10 such a browser has no role and every action is refused. The
// host's own protocol also mints the code `wsp init` uses for the browser it opens (`pair.issue` with `here: true`),
// which pairs as the owner. This asks for that code over the host's local websocket, authenticated with the host
// token, so run it on the computer the host runs on.
//
//   node wsp-owner-code.mjs            # WSP_HOME (default ~/.wsp), WSP_PORT (default 4400)
//
// Contract: prints "owner code <CODE>  expires <ISO time>" and exits 0, or prints the refusal and exits 1.
// The code is spent by the first browser that redeems it; `wsp host devices` lists what took one.
import { readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

const home = process.env.WSP_HOME || join(homedir(), ".wsp");
const port = process.env.WSP_PORT || "4400";
const token = readFileSync(join(home, "host-token"), "utf8").trim();
const ws = new WebSocket(`ws://127.0.0.1:${port}/ws`);
const fail = (m) => { console.error(m); process.exit(1); };
setTimeout(() => fail("no answer from the wsp host (is `wsp up` running?)"), 10000).unref();

ws.onerror = () => fail(`cannot reach the wsp host at 127.0.0.1:${port}`);
ws.onopen = () => ws.send(JSON.stringify({ id: 1, op: "auth", token }));
ws.onmessage = (e) => {
  const m = JSON.parse(e.data);
  if (m.id === 1) {
    if (!m.ok) fail(`auth refused: ${m.error ?? JSON.stringify(m)}`);
    ws.send(JSON.stringify({ id: 2, op: "pair.issue", here: true }));
  }
  if (m.id === 2) {
    if (!m.ok) fail(`pair.issue refused: ${m.error ?? JSON.stringify(m)}`);
    console.log(`owner code ${m.code}  expires ${new Date(m.expiresAt).toISOString()}`);
    ws.close();
    process.exit(0);
  }
};
