#!/usr/bin/env python3
"""A proxy that lets nothing through: every request (CONNECT host:port, or a plain-HTTP GET) is appended to the log
and answered 403. Set as HTTPS_PROXY/HTTP_PROXY (NO_PROXY=127.0.0.1) in a live harness check, it shows anything a run
tries to reach beyond the local fake model: a vendor's API, a model catalog, an npm registry.

  logging_proxy.py <log>

Prints its port on the first line, then serves until killed."""
import http.server, sys
LOG = sys.argv[1]
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def refuse(self):
        open(LOG, "a").write(f"{self.command} {self.path}\n")
        self.send_response(403); self.send_header("Content-Length", "0"); self.end_headers()
    do_CONNECT = do_GET = do_POST = do_PUT = do_HEAD = refuse
s = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
print(s.server_address[1], flush=True)
s.serve_forever()
