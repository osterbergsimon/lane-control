#!/usr/bin/env python3
"""Stand-in for an OpenAI-compatible gateway (LiteLLM-like) on 127.0.0.1, for project-setup --suggest-tiers.

  fake_gateway.py <spec.json> <request log>

spec.json: {"key": <the bearer key it wants, or null>, "models": [<alias>, ...],
            "info": {<alias>: <model behind it> or {"model": ..., "mode": ...}} or null (null: no /model/info route,
                     as on a gateway that is not LiteLLM),
            "redirect": <a URL every GET is sent on to with 302>, "garbage": true (answer with a line that is not HTTP),
            "drip": true (send the headers, then one byte of body a second, forever)}
Prints its port on the first line, then serves until killed. Each request is appended to the log as one JSON line
({"path": ..., "auth": "ok" | "missing" | "wrong"}); the key itself is never logged. A wrong or missing key gets 401.
"""
import http.server
import json
import sys
import time

SPEC = json.load(open(sys.argv[1]))
LOG = sys.argv[2]


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        got = self.headers.get("Authorization")
        want = SPEC.get("key")
        auth = "missing" if not got else "ok" if want and got == f"Bearer {want}" else "wrong"
        with open(LOG, "a") as f:
            f.write(json.dumps({"path": self.path, "auth": auth}) + "\n")
        if SPEC.get("garbage"):
            self.wfile.write(b"GARBAGE\r\n\r\n")
            return
        if SPEC.get("redirect"):
            self.send_response(302)
            self.send_header("Location", SPEC["redirect"] + self.path)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if SPEC.get("drip"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", "1000000")
            self.end_headers()
            try:
                while True:
                    self.wfile.write(b" ")
                    self.wfile.flush()
                    time.sleep(1)
            except OSError:
                return
        if want and auth != "ok":
            return self.reply(401, {"error": "unauthorized"})
        if self.path == "/v1/models":
            return self.reply(200, {"object": "list", "data": [{"id": m, "object": "model"} for m in SPEC["models"]]})
        if self.path == "/model/info" and SPEC.get("info") is not None:
            rows = []
            for a, m in SPEC["info"].items():
                m = m if isinstance(m, dict) else {"model": m}
                row = {"model_name": a, "litellm_params": {"model": m["model"]}}
                if "mode" in m:
                    row["model_info"] = {"mode": m["mode"]}
                rows.append(row)
            return self.reply(200, {"data": rows})
        self.reply(404, {"error": "not found"})


server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
print(server.server_address[1], flush=True)
server.serve_forever()
