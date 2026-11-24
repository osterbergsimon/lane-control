#!/usr/bin/env python3
"""Stand-in for an OpenAI-compatible gateway (LiteLLM-like) on 127.0.0.1, for project-setup --suggest-tiers.

  fake_gateway.py <spec.json> <request log>

spec.json: {"key": <the bearer key it wants, or null>, "models": [<alias>, ...],
            "info": {<alias>: <model behind it>} or null (null: no /model/info route, as on a gateway that is not LiteLLM)}
Prints its port on the first line, then serves until killed. Each request is appended to the log as one JSON line
({"path": ..., "auth": "ok" | "missing" | "wrong"}); the key itself is never logged. A wrong or missing key gets 401.
"""
import http.server
import json
import sys

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
        if want and auth != "ok":
            return self.reply(401, {"error": "unauthorized"})
        if self.path == "/v1/models":
            return self.reply(200, {"object": "list", "data": [{"id": m, "object": "model"} for m in SPEC["models"]]})
        if self.path == "/model/info" and SPEC.get("info") is not None:
            return self.reply(200, {"data": [{"model_name": a, "litellm_params": {"model": m}}
                                             for a, m in SPEC["info"].items()]})
        self.reply(404, {"error": "not found"})


server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
print(server.server_address[1], flush=True)
server.serve_forever()
