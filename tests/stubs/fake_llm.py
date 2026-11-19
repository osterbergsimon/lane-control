#!/usr/bin/env python3
"""Stand-in for a model provider: an OpenAI-compatible chat server on 127.0.0.1 for live harness checks.

  fake_llm.py <request log>

Prints its port on the first line, then serves until killed. Every POST body is appended to the log as one JSON
line ({"path": ..., "body": ...}); every answer is "ok" (streamed when the request asks for a stream). No real
provider is ever contacted, so no code leaves the machine.
"""
import http.server
import json
import sys

LOG = sys.argv[1]
USAGE = {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, obj, ctype="application/json"):
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.reply({"object": "list", "data": [{"id": "m", "object": "model"}]})

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        with open(LOG, "a") as f:
            f.write(json.dumps({"path": self.path, "body": raw.decode("utf8", "replace")}) + "\n")
        try:
            req = json.loads(raw)
        except ValueError:
            req = {}
        if not req.get("stream"):
            self.reply({"id": "c", "object": "chat.completion", "created": 0, "model": "m", "usage": USAGE,
                        "choices": [{"index": 0, "finish_reason": "stop",
                                     "message": {"role": "assistant", "content": "ok"}}]})
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for delta in ({"role": "assistant", "content": "ok"}, {}):
            chunk = {"id": "c", "object": "chat.completion.chunk", "created": 0, "model": "m",
                     "choices": [{"index": 0, "delta": delta, "finish_reason": None if delta else "stop"}]}
            if not delta:
                chunk["usage"] = USAGE
            self.wfile.write(b"data: " + json.dumps(chunk).encode() + b"\n\n")
        self.wfile.write(b"data: [DONE]\n\n")


server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
print(server.server_address[1], flush=True)
server.serve_forever()
