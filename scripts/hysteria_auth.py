#!/usr/bin/env python3
"""Loopback-only Hysteria2 HTTP authentication backend.

This process intentionally has no request logging.  Hysteria sends the
authentication payload here, and the response contains only an allow flag and
an internal stats identifier.
"""

from __future__ import annotations

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.models import make_session_factory  # noqa: E402
from app.proxy_adapters import authenticate_hysteria  # noqa: E402


class Handler(BaseHTTPRequestHandler):
    server_version = "sub-app-auth"
    sys_version = ""

    def log_message(self, _format, *_args):
        return

    def _write(self, status: int, payload: dict):
        body = json.dumps(payload, ensure_ascii=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):  # noqa: N802
        if self.path != "/auth":
            self._write(404, {"ok": False})
            return
        try:
            length = min(int(self.headers.get("Content-Length", "0")), 65536)
            payload = json.loads(self.rfile.read(length))
            auth = str(payload.get("auth", ""))
        except (ValueError, TypeError, json.JSONDecodeError):
            self._write(200, {"ok": False})
            return
        db = self.server.session_factory()
        try:
            stats_id = authenticate_hysteria(db, auth)
            self._write(200, {"ok": bool(stats_id), "id": stats_id or ""})
        finally:
            db.close()


def main():
    db_path = os.environ.get("SUB_APP_DB", os.path.join(ROOT, "data.db"))
    factory = make_session_factory(db_path)
    server = ThreadingHTTPServer(("127.0.0.1", 5001), Handler)
    server.session_factory = factory
    server.serve_forever()


if __name__ == "__main__":
    main()
