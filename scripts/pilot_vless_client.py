#!/usr/bin/env python3
"""Bounded live Reality/VLESS handshake check for the US pilot."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from sqlalchemy import select

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.converter import parse_uri  # noqa: E402
from app.credentials import credential_values  # noqa: E402
from app.models import (  # noqa: E402
    Friend,
    Node,
    UserNodeCredential,
    make_session_factory,
)


def _load_env():
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            os.environ.setdefault(key, value.strip().strip("'\""))


def _safe_flags(text: str):
    lower = text.lower()
    return {
        key: token in lower
        for key, token in {
            "error": "error",
            "fatal": "fatal",
            "reality": "reality",
            "vless": "vless",
            "tls": "tls",
            "timeout": "timeout",
            "connection": "connection",
            "unknown_field": "unknown field",
        }.items()
    }


def main():
    _load_env()
    pilot_port = int(os.environ.get("VLESS_PILOT_PORT", "28081"))
    pilot_uid = os.environ.get("VLESS_PILOT_UID", "Guan")
    pilot_node = int(os.environ.get("VLESS_PILOT_NODE", "11"))
    factory = make_session_factory(str(ROOT / "data.db"))
    db = factory()
    try:
        friend = db.scalar(select(Friend).where(Friend.uid == pilot_uid))
        node = db.get(Node, pilot_node)
        parsed = parse_uri(node.uri)
        if not friend or not node or not parsed or parsed["scheme"] != "vless":
            print("vless_pilot_input_invalid")
            return 1
        if os.environ.get("VLESS_PILOT_SHARED") == "1":
            uuid = parsed["user"]
        else:
            row = db.scalar(
                select(UserNodeCredential)
                .where(
                    UserNodeCredential.friend_id == friend.id,
                    UserNodeCredential.node_id == pilot_node,
                    UserNodeCredential.protocol == "vless",
                    UserNodeCredential.status.in_(["active", "grace"]),
                    UserNodeCredential.revoked_at.is_(None),
                )
                .order_by(UserNodeCredential.version.desc())
                .limit(1)
            )
            if row is None:
                print("vless_pilot_credential_missing")
                return 1
            uuid = credential_values(row)["uuid"]
        params = parsed["params"]
        outbound = {
            "type": "vless",
            "server": os.environ.get("VLESS_PILOT_HOST", parsed["host"]),
            "server_port": parsed["port"],
            "uuid": uuid,
            "tls": {
                "enabled": True,
                "server_name": params.get("sni", ""),
                "reality": {
                    "enabled": True,
                    "public_key": params.get("pbk", ""),
                    "short_id": params.get("sid", ""),
                },
                "utls": {"enabled": True, "fingerprint": params.get("fp", "chrome")},
            },
        }
        if params.get("flow"):
            outbound["flow"] = params["flow"]
        config = {
            "log": {"level": "error"},
            "inbounds": [
                {
                    "type": "mixed",
                    "listen": "127.0.0.1",
                    "listen_port": pilot_port,
                }
            ],
            "outbounds": [outbound],
        }
    finally:
        db.close()

    config_path = Path(
        tempfile.mktemp(prefix="vless-pilot.", suffix=".json", dir="/tmp")
    )
    log_path = Path(tempfile.mktemp(prefix="vless-pilot-log.", dir="/tmp"))
    config_path.write_text(json_dump(config), encoding="utf-8")
    os.chmod(config_path, 0o600)
    process = subprocess.Popen(
        ["/usr/local/bin/sing-box-reality", "run", "-c", str(config_path)],
        stdout=log_path.open("w", encoding="utf-8"),
        stderr=subprocess.STDOUT,
    )
    try:
        time.sleep(4)
        if process.poll() is not None:
            text = log_path.read_text(encoding="utf-8", errors="replace")
            print("vless_pilot_process_failed", "log_flags", _safe_flags(text))
            return 1
        response = subprocess.run(
            [
                "curl",
                "-fsS",
                "--socks5-hostname",
                f"127.0.0.1:{pilot_port}",
                "--max-time",
                "15",
                os.environ.get("VLESS_PILOT_URL", "https://sub.m1n6.uk/"),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=18,
        )
        text = log_path.read_text(encoding="utf-8", errors="replace")
        print(
            "vless_external_handshake_ok",
            response.returncode == 0,
            "log_flags",
            _safe_flags(text),
        )
        return 0 if response.returncode == 0 else 1
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
        config_path.unlink(missing_ok=True)
        log_path.unlink(missing_ok=True)


def json_dump(value):
    import json

    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


if __name__ == "__main__":
    raise SystemExit(main())
