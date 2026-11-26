#!/usr/bin/env python3
"""Stand-in for a model provider that asks for one shell tool call per user message containing RUN-TOOL: an
OpenAI-compatible chat server on 127.0.0.1 for live harness checks.

  fake_tool_llm.py <request log>

Prints its port on the first line, then serves until killed. The tool call is $LANE_FAKE_TOOL (default shell) with
{"command": $LANE_FAKE_CMD} (or the JSON in $LANE_FAKE_ARGS); once its result is in the conversation the answer is
"done". Every POST body is appended to the log as one JSON line; with $LANE_FAKE_CHAT_ONLY set, a POST to any other
route than chat completions is logged and answered 404. No real provider is ever contacted."""
import http.server, json, os, sys
LOG = sys.argv[1]
CMD = os.environ.get("LANE_FAKE_CMD", "echo hi")
USAGE = {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        b = json.dumps({"object": "list", "data": [{"id": "m", "object": "model"}]}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        open(LOG, "a").write(json.dumps({"path": self.path, "body": raw.decode("utf8", "replace")}) + "\n")
        if os.environ.get("LANE_FAKE_CHAT_ONLY") and not self.path.endswith("/chat/completions"):
            self.send_response(404); self.send_header("Content-Length", "0"); self.end_headers(); return
        req = json.loads(raw)
        msgs = req.get("messages", [])
        last_user, idx = "", -1
        for i, m in enumerate(msgs):
            if m.get("role") == "user":
                c = m.get("content"); last_user = c if isinstance(c, str) else json.dumps(c); idx = i
        has_tool_result = any(m.get("role") == "tool" for m in msgs[idx + 1:])
        want_tool = (not has_tool_result) and "RUN-TOOL" in last_user and req.get("tools")
        self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.end_headers()
        def chunk(delta, fin=None, usage=False):
            c = {"id": "c", "object": "chat.completion.chunk", "created": 0, "model": "m", "choices": [{"index": 0, "delta": delta, "finish_reason": fin}]}
            if usage: c["usage"] = USAGE
            self.wfile.write(b"data: " + json.dumps(c).encode() + b"\n\n")
        if want_tool:
            name = os.environ.get("LANE_FAKE_TOOL", "shell")
            args = os.environ.get("LANE_FAKE_ARGS") or json.dumps({"command": CMD})
            chunk({"role": "assistant", "content": None, "tool_calls": [{"index": 0, "id": "call_1", "type": "function", "function": {"name": name, "arguments": args}}]})
            chunk({}, "tool_calls", True)
        else:
            chunk({"role": "assistant", "content": "done"}); chunk({}, "stop", True)
        self.wfile.write(b"data: [DONE]\n\n")
s = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
print(s.server_address[1], flush=True)
s.serve_forever()
