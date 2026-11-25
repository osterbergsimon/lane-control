#!/usr/bin/env python3
"""A fake LiteLLM gateway for the testbed: OpenAI-compatible, so OpenCode lanes run against it at no cost.

  gateway.py [--host 127.0.0.1] [--port 4000] [--key KEY] [--log FILE] [--alias ALIAS=MODEL[:MODE]]...

Built on tests/stubs/fake_llm.py (same answers), plus what LiteLLM adds and lane-control reads:
  GET  /v1/models (and /models)        the aliases, as {"object": "list", "data": [{"id": alias}, ...]}
  GET  /model/info (and /v1/model/info) each alias with the made-up model behind it (litellm_params.model) and its
                                       model_info.mode (chat unless given, e.g. embedding)
  GET  /health/liveliness              "I'm alive!"
  POST /v1/chat/completions (and /chat/completions)   "ok" (streamed when asked), or one shell tool call when the last
                                       user message holds RUN-TOOL: <command> and the request offers tools
Default aliases: chat -> testbed/fake-chat-small, code -> testbed/fake-code-large, embed -> testbed/fake-embed (mode
embedding). With --key every request needs `Authorization: Bearer KEY` (401 otherwise); the key is never logged.
--port 0 picks a free port; the first stdout line is the port. Each request is one JSON line in --log
({"path", "auth", "model"}). No real provider is ever contacted.
"""
import argparse
import http.server
import json
import re
import sys

USAGE = {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}
DEFAULT_ALIASES = ["chat=testbed/fake-chat-small", "code=testbed/fake-code-large", "embed=testbed/fake-embed:embedding"]


def parse_aliases(items):
    out = {}
    for it in items:
        alias, _, rest = it.partition("=")
        model, _, mode = rest.partition(":")
        if not alias or not model:
            raise SystemExit(f"gateway.py: --alias takes ALIAS=MODEL[:MODE], got {it!r}")
        out[alias] = {"model": model, "mode": mode or "chat"}
    return out


def make_handler(aliases, key, log):
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def note(self, auth, model=None):
            if log:
                with open(log, "a") as f:
                    f.write(json.dumps({"path": self.path, "auth": auth, "model": model}) + "\n")

        def reply(self, code, obj):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def authed(self, model=None):
            got = self.headers.get("Authorization")
            auth = "missing" if not got else "ok" if key and got == f"Bearer {key}" else "wrong" if key else "ok"
            self.note(auth, model)
            if key and auth != "ok":
                self.reply(401, {"error": {"message": "Authentication Error, invalid key", "type": "auth_error"}})
                return False
            return True

        def do_GET(self):
            path = self.path.split("?")[0]
            if path in ("/health/liveliness", "/health/readiness"):
                self.note("none")
                return self.reply(200, "I'm alive!")
            if not self.authed():
                return
            if path in ("/v1/models", "/models"):
                return self.reply(200, {"object": "list", "data": [
                    {"id": a, "object": "model", "created": 0, "owned_by": "openai"} for a in aliases]})
            if path in ("/model/info", "/v1/model/info"):
                return self.reply(200, {"data": [
                    {"model_name": a, "litellm_params": {"model": m["model"]},
                     "model_info": {"mode": m["mode"], "id": f"testbed-{a}"}} for a, m in aliases.items()]})
            self.reply(404, {"error": {"message": f"no route {path}"}})

        def do_POST(self):
            raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            try:
                req = json.loads(raw)
            except ValueError:
                req = {}
            model = req.get("model") if isinstance(req, dict) else None
            if not self.authed(model):
                return
            path = self.path.split("?")[0]
            if path not in ("/v1/chat/completions", "/chat/completions"):
                return self.reply(404, {"error": {"message": f"no route {path}"}})
            if model not in aliases or aliases[model]["mode"] != "chat":
                return self.reply(400, {"error": {"message": f"Invalid model name passed in model={model}"}})
            msgs = req.get("messages") or []
            last_user, idx = "", -1
            for i, m in enumerate(msgs):
                if isinstance(m, dict) and m.get("role") == "user":
                    c = m.get("content")
                    last_user, idx = (c if isinstance(c, str) else json.dumps(c)), i
            tool_done = any(isinstance(m, dict) and m.get("role") == "tool" for m in msgs[idx + 1:])
            cmd = re.search(r"RUN-TOOL: ([^\n\"\\]+)", last_user)
            call = None
            if cmd and req.get("tools") and not tool_done:
                call = {"index": 0, "id": "call_1", "type": "function",
                        "function": {"name": "bash", "arguments": json.dumps({"command": cmd.group(1).strip(),
                                                                              "description": "testbed tool call"})}}
            if not req.get("stream"):
                msg = {"role": "assistant", "content": None if call else "ok"}
                if call:
                    msg["tool_calls"] = [{k: v for k, v in call.items() if k != "index"}]
                return self.reply(200, {"id": "c", "object": "chat.completion", "created": 0, "model": model,
                                        "usage": USAGE, "choices": [{"index": 0, "message": msg,
                                                                     "finish_reason": "tool_calls" if call else "stop"}]})
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            first = {"role": "assistant", "content": None, "tool_calls": [call]} if call else {"role": "assistant", "content": "ok"}
            for delta in (first, {}):
                chunk = {"id": "c", "object": "chat.completion.chunk", "created": 0, "model": model,
                         "choices": [{"index": 0, "delta": delta,
                                      "finish_reason": None if delta else ("tool_calls" if call else "stop")}]}
                if not delta:
                    chunk["usage"] = USAGE
                self.wfile.write(b"data: " + json.dumps(chunk).encode() + b"\n\n")
            self.wfile.write(b"data: [DONE]\n\n")

    return Handler


def main(argv=None):
    ap = argparse.ArgumentParser(description="fake LiteLLM gateway (testbed)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=4000)
    ap.add_argument("--key")
    ap.add_argument("--log")
    ap.add_argument("--alias", action="append")
    a = ap.parse_args(argv)
    aliases = parse_aliases(a.alias or DEFAULT_ALIASES)
    server = http.server.ThreadingHTTPServer((a.host, a.port), make_handler(aliases, a.key, a.log))
    print(server.server_address[1], flush=True)
    server.serve_forever()


if __name__ == "__main__":
    sys.exit(main())
