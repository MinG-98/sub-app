#!/usr/bin/env python3
"""Collect per-user counters from the local US proxy cores."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import yaml
from sqlalchemy import delete, select

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.credentials import credential_stats_id  # noqa: E402
from app.models import (  # noqa: E402
    CollectorRun,
    FlowRecord,
    Friend,
    ProxyTrafficCounter,
    UserNodeCredential,
    make_session_factory,
    utcnow,
)

STATUS_PATH = Path(
    os.environ.get(
        "SUB_APP_PROXY_STATUS", "/var/lib/sub-app/proxy-collector-status.json"
    )
)
RETENTION_DAYS = 30
VLESS_STATS_BINARY = os.environ.get(
    "SUB_APP_VLESS_STATS_BINARY", "/usr/local/libexec/sub-app-vless-stats"
)
VLESS_STAT_RE = re.compile(r"^user>>>([^>]+)>>>traffic>>>(uplink|downlink)$")


def _bucket(now):
    now = now.astimezone(timezone.utc).replace(tzinfo=None)
    return now.replace(minute=now.minute - now.minute % 5, second=0, microsecond=0)


def _non_negative(current, previous):
    if previous is None or current < previous:
        return 0
    return current - previous


def _hysteria_endpoint():
    cfg = yaml.safe_load(Path("/etc/hysteria-us-8443/config.yaml").read_text()) or {}
    stats = cfg.get("trafficStats") or {}
    listen = str(stats.get("listen", "127.0.0.1:9999"))
    if ":" not in listen:
        raise RuntimeError("Hysteria traffic API 地址无效")
    host, port = listen.rsplit(":", 1)
    host = host or "127.0.0.1"
    return f"http://{host}:{int(port)}", str(stats.get("secret", ""))


def _hysteria_stats():
    base, secret = _hysteria_endpoint()
    if not secret:
        raise RuntimeError("Hysteria traffic API 未配置 secret")
    response = httpx.get(
        f"{base}/traffic",
        headers={"Authorization": secret},
        timeout=12,
    )
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise RuntimeError("Hysteria traffic API 返回格式错误")
    return data


def collect_hysteria(db, now):
    stats = _hysteria_stats()
    rows = db.execute(
        select(UserNodeCredential, Friend)
        .join(Friend, Friend.id == UserNodeCredential.friend_id)
        .where(
            UserNodeCredential.node_id == 4,
            UserNodeCredential.protocol == "hysteria2",
            UserNodeCredential.revoked_at.is_(None),
            Friend.enabled.is_(True),
            Friend.per_user_credentials.is_(True),
        )
    ).all()
    by_id = {}
    current_time = utcnow().replace(tzinfo=None)
    for row, _friend in rows:
        if row.status == "grace" and (
            not row.grace_until or row.grace_until <= current_time
        ):
            continue
        if row.status in {"active", "grace"}:
            by_id[credential_stats_id(row)] = row

    bucket = _bucket(now)
    written = 0
    for client_id, value in stats.items():
        row = by_id.get(str(client_id))
        if row is None or not isinstance(value, dict):
            continue
        current_in = max(0, int(value.get("rx", 0) or 0))
        current_out = max(0, int(value.get("tx", 0) or 0))
        counter = db.scalar(
            select(ProxyTrafficCounter).where(
                ProxyTrafficCounter.source == "hysteria2",
                ProxyTrafficCounter.node_id == 4,
                ProxyTrafficCounter.credential_key == str(client_id),
            )
        )
        previous_in = counter.last_bytes_in if counter else None
        previous_out = counter.last_bytes_out if counter else None
        delta_in = _non_negative(current_in, previous_in)
        delta_out = _non_negative(current_out, previous_out)
        if counter is None:
            counter = ProxyTrafficCounter(
                source="hysteria2",
                node_id=4,
                friend_id=row.friend_id,
                credential_key=str(client_id),
            )
            db.add(counter)
        counter.friend_id = row.friend_id
        counter.last_bytes_in = current_in
        counter.last_bytes_out = current_out
        counter.updated_at = now.replace(tzinfo=None)
        key = f"hysteria2:4:{client_id}:{bucket.isoformat()}"
        flow = db.scalar(
            select(FlowRecord).where(
                FlowRecord.source == "hysteria2",
                FlowRecord.sample_key == key,
            )
        )
        if flow is None:
            flow = FlowRecord(
                node_id=4,
                friend_id=row.friend_id,
                bucket=bucket,
                source="hysteria2",
                sample_key=key,
                bytes_in=delta_in,
                bytes_out=delta_out,
            )
            db.add(flow)
        else:
            flow.friend_id = row.friend_id
            flow.bytes_in += delta_in
            flow.bytes_out += delta_out
        written += 1
    return {
        "status": "success",
        "stats_clients": len(stats),
        "samples_written": written,
    }


def _vless_stats():
    result = subprocess.run(
        [VLESS_STATS_BINARY],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    if result.returncode:
        raise RuntimeError("VLESS V2Ray API 查询失败")
    try:
        payload = json.loads(result.stdout)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("VLESS V2Ray API 返回格式错误") from exc
    stats = payload.get("stats") if isinstance(payload, dict) else None
    if not isinstance(stats, list):
        raise RuntimeError("VLESS V2Ray API 返回格式错误")
    return stats


def collect_vless(db, now):
    """Collect named VLESS user counters from the loopback V2Ray API."""

    stats = _vless_stats()
    rows = db.execute(
        select(UserNodeCredential, Friend)
        .join(Friend, Friend.id == UserNodeCredential.friend_id)
        .where(
            UserNodeCredential.node_id == 11,
            UserNodeCredential.protocol == "vless",
            UserNodeCredential.revoked_at.is_(None),
            Friend.enabled.is_(True),
            Friend.per_user_credentials.is_(True),
        )
    ).all()
    by_id = {}
    current_time = utcnow().replace(tzinfo=None)
    for row, _friend in rows:
        if row.status == "grace" and (
            not row.grace_until or row.grace_until <= current_time
        ):
            continue
        if row.status in {"active", "grace"}:
            by_id[credential_stats_id(row)] = row

    counters = {}
    for item in stats:
        if not isinstance(item, dict):
            continue
        match = VLESS_STAT_RE.match(str(item.get("name", "")))
        if not match:
            continue
        stats_id, direction = match.groups()
        row = by_id.get(stats_id)
        if row is None:
            continue
        try:
            value = max(0, int(item.get("value", 0) or 0))
        except (TypeError, ValueError):
            continue
        entry = counters.setdefault(stats_id, {"row": row, "in": 0, "out": 0})
        entry["in" if direction == "uplink" else "out"] = value

    bucket = _bucket(now)
    written = 0
    for stats_id, entry in counters.items():
        row = entry["row"]
        current_in = entry["in"]
        current_out = entry["out"]
        counter = db.scalar(
            select(ProxyTrafficCounter).where(
                ProxyTrafficCounter.source == "vless",
                ProxyTrafficCounter.node_id == 11,
                ProxyTrafficCounter.credential_key == stats_id,
            )
        )
        previous_in = counter.last_bytes_in if counter else None
        previous_out = counter.last_bytes_out if counter else None
        delta_in = _non_negative(current_in, previous_in)
        delta_out = _non_negative(current_out, previous_out)
        if counter is None:
            counter = ProxyTrafficCounter(
                source="vless",
                node_id=11,
                friend_id=row.friend_id,
                credential_key=stats_id,
            )
            db.add(counter)
        counter.friend_id = row.friend_id
        counter.last_bytes_in = current_in
        counter.last_bytes_out = current_out
        counter.updated_at = now.replace(tzinfo=None)
        key = f"vless:11:{stats_id}:{bucket.isoformat()}"
        flow = db.scalar(
            select(FlowRecord).where(
                FlowRecord.source == "vless",
                FlowRecord.sample_key == key,
            )
        )
        if flow is None:
            flow = FlowRecord(
                node_id=11,
                friend_id=row.friend_id,
                bucket=bucket,
                source="vless",
                sample_key=key,
                bytes_in=delta_in,
                bytes_out=delta_out,
            )
            db.add(flow)
        else:
            flow.friend_id = row.friend_id
            flow.bytes_in += delta_in
            flow.bytes_out += delta_out
        written += 1
    return {
        "status": "success",
        "stats_users": len(counters),
        "samples_written": written,
    }


def _write_status(payload):
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(
        prefix="proxy-status.", suffix=".json", dir=str(STATUS_PATH.parent)
    )
    path = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=True, separators=(",", ":"))
            handle.write("\n")
        os.chmod(path, 0o600)
        os.replace(path, STATUS_PATH)
    finally:
        path.unlink(missing_ok=True)


def main():
    db_path = os.environ.get("SUB_APP_DB", os.path.join(ROOT, "data.db"))
    factory = make_session_factory(db_path)
    db = factory()
    now = datetime.now(timezone.utc)
    run = CollectorRun(
        started_at=now.replace(tzinfo=None), source="proxy", status="running"
    )
    db.add(run)
    db.commit()
    results = {}
    try:
        for name, callback in (
            ("hysteria2", collect_hysteria),
            ("vless", collect_vless),
        ):
            try:
                results[name] = callback(db, now)
                db.commit()
            except Exception as exc:
                db.rollback()
                results[name] = {
                    "status": "error",
                    "samples_written": 0,
                    "error": type(exc).__name__,
                }
        db.execute(
            delete(FlowRecord).where(
                FlowRecord.source.in_(["hysteria2", "vless"]),
                FlowRecord.bucket
                < (now.replace(tzinfo=None) - timedelta(days=RETENTION_DAYS)),
            )
        )
        statuses = [item.get("status") for item in results.values()]
        run.status = (
            "success"
            if statuses == ["success", "not_ready"]
            or all(s == "success" for s in statuses)
            else "partial"
        )
        run.samples_written = sum(
            int(item.get("samples_written", 0)) for item in results.values()
        )
        run.nodes_total = 2
        run.finished_at = now.replace(tzinfo=None)
        run.error = "; ".join(
            f"{name}:{item.get('error')}"
            for name, item in results.items()
            if item.get("error")
        )[:1000]
        db.commit()
        _write_status({"at": now.isoformat(), "status": run.status, "results": results})
        print(
            json.dumps(
                {"ok": True, "status": run.status, "results": results},
                ensure_ascii=True,
            )
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
