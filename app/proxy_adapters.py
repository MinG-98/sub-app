"""Local proxy-core adapters used by the per-user credential rollout.

The US adapters are local.  The other supported VLESS/Hysteria2 nodes are
controlled by a root-only node agent and are not enabled until that agent has
reported a healthy, applied generation.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from app.credentials import credential_stats_id, credential_values
from app.models import Friend, Node, NodeAgent, UserNodeCredential, utcnow

ADAPTER_ENV_FILE = Path(
    os.environ.get("SUB_APP_PROXY_ADAPTERS_ENV", "/etc/sub-app/proxy-adapters.env")
)
VLESS_CONFIG = Path(
    os.environ.get("SUB_APP_VLESS_CONFIG", "/etc/sing-box-reality/config.json")
)
VLESS_BINARY = os.environ.get("SUB_APP_VLESS_BINARY", "/usr/local/bin/sing-box-reality")
VLESS_SERVICE = os.environ.get("SUB_APP_VLESS_SERVICE", "sing-box-reality.service")
VLESS_LEGACY_HOURS = 24

LOCAL_SUPPORTED_NODES = {4, 11}

REMOTE_AGENT_NODES = {
    5: {"protocol": "vless", "source": "vless", "label": "VLESS 节点 Agent"},
    6: {
        "protocol": "hysteria2",
        "source": "hysteria2",
        "label": "Hysteria2 节点 Agent",
    },
    7: {
        "protocol": "hysteria2",
        "source": "hysteria2",
        "label": "Hysteria2 节点 Agent",
    },
    8: {
        "protocol": "hysteria2",
        "source": "hysteria2",
        "label": "Hysteria2 节点 Agent",
    },
    9: {
        "protocol": "hysteria2",
        "source": "hysteria2",
        "label": "Hysteria2 节点 Agent",
    },
    10: {
        "protocol": "hysteria2",
        "source": "hysteria2",
        "label": "Hysteria2 节点 Agent",
    },
    12: {"protocol": "vless", "source": "vless", "label": "VLESS 节点 Agent"},
    13: {"protocol": "vless", "source": "vless", "label": "VLESS 节点 Agent"},
    14: {"protocol": "vless", "source": "vless", "label": "VLESS 节点 Agent"},
    15: {"protocol": "vless", "source": "vless", "label": "VLESS 节点 Agent"},
    16: {"protocol": "vless", "source": "vless", "label": "VLESS 节点 Agent"},
    17: {"protocol": "vless", "source": "vless", "label": "VLESS 节点 Agent"},
    18: {
        "protocol": "hysteria2",
        "source": "hysteria2",
        "label": "Hysteria2 节点 Agent",
    },
}

SUPPORTED_NODES = {
    4: {"protocol": "hysteria2", "source": "hysteria2", "label": "Hysteria2 HTTP auth"},
    11: {"protocol": "vless", "source": "vless", "label": "VLESS V2Ray API"},
    **REMOTE_AGENT_NODES,
}


def _read_env(path: Path = ADAPTER_ENV_FILE) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return values
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("'\"")
    return values


def adapter_marker() -> dict[str, str]:
    return _read_env()


def adapter_ready(node_id: int, db=None) -> bool:
    values = adapter_marker()
    if node_id == 4:
        return values.get("HY2_HTTP_AUTH") == "1"
    if node_id == 11:
        return values.get("VLESS_V2RAY_API") == "1"
    if node_id in REMOTE_AGENT_NODES and db is not None:
        agent = db.scalar(select(NodeAgent).where(NodeAgent.node_id == node_id))
        if not agent or agent.status != "ready" or not agent.last_seen:
            return False
        last_seen = _db_utc(agent.last_seen)
        return bool(
            last_seen
            and (utcnow().replace(tzinfo=None) - last_seen).total_seconds() <= 180
        )
    return False


def node_per_user_enabled(node: Node) -> bool:
    """Local US rollout stays enabled; remote nodes require an explicit flag."""
    return node.id in LOCAL_SUPPORTED_NODES or bool(node.per_user_enabled)


def _db_utc(value: datetime | None) -> datetime | None:
    """Normalize SQLite-naive and in-session aware UTC timestamps alike."""
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def legacy_hysteria_allowed(auth: str) -> bool:
    values = adapter_marker()
    until = values.get("HY2_LEGACY_UNTIL", "")
    digest = values.get("HY2_LEGACY_SHA256", "")
    if not until or not digest:
        return False
    try:
        allowed_until = datetime.fromisoformat(until.replace("Z", "+00:00"))
    except ValueError:
        return False
    return utcnow() < allowed_until and hmac.compare_digest(
        hashlib.sha256(auth.encode("utf-8")).hexdigest(), digest
    )


def authenticate_hysteria(db, auth: str) -> str | None:
    """Return the stats ID for a Hysteria auth payload, without logging it."""
    if legacy_hysteria_allowed(auth):
        return "legacy-shared"
    if ":" not in auth:
        return None
    username, password = auth.split(":", 1)
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
    # SQLite returns DateTime columns without tzinfo; compare both sides in
    # naive UTC while keeping utcnow() itself timezone-aware for API output.
    now = utcnow().replace(tzinfo=None)
    for row, friend in rows:
        grace_until = _db_utc(row.grace_until)
        if row.status == "grace" and (not grace_until or grace_until <= now):
            continue
        if row.status not in {"active", "grace"}:
            continue
        values = credential_values(row)
        if values.get("username") == username and hmac.compare_digest(
            values.get("password", ""), password
        ):
            return credential_stats_id(row)
    return None


def capability(node: Node, db=None) -> dict:
    info = SUPPORTED_NODES.get(node.id)
    if not info or node.protocol != info["protocol"]:
        return {
            "supported": False,
            "ready": False,
            "source": "shared",
            "label": "暂不支持按用户计量",
        }
    return {
        "supported": True,
        "ready": adapter_ready(node.id, db),
        "source": info["source"],
        "label": (
            info["label"]
            if node.id in LOCAL_SUPPORTED_NODES or adapter_ready(node.id, db)
            else "等待节点 Agent 上报并应用"
        ),
    }


def _run_check(config_path: Path) -> None:
    result = subprocess.run(
        [VLESS_BINARY, "check", "-c", str(config_path)],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    if result.returncode:
        raise RuntimeError("sing-box 配置校验失败")


def _live_vless_rows(db, extra: UserNodeCredential | None = None):
    rows = db.scalars(
        select(UserNodeCredential, Friend)
        .join(Friend, Friend.id == UserNodeCredential.friend_id)
        .where(
            UserNodeCredential.node_id == 11,
            UserNodeCredential.protocol == "vless",
            UserNodeCredential.revoked_at.is_(None),
            Friend.enabled.is_(True),
            Friend.per_user_credentials.is_(True),
        )
        .order_by(UserNodeCredential.friend_id, UserNodeCredential.version)
    ).all()
    result = [row for row in rows if row.status == "active"]
    now = utcnow().replace(tzinfo=None)
    for row in rows:
        grace_until = _db_utc(row.grace_until)
        if row.status == "grace" and grace_until and grace_until > now:
            result.append(row)
    if extra is not None and extra not in result:
        result.append(extra)
    return result


def sync_vless_config(db, extra: UserNodeCredential | None = None) -> None:
    """Atomically reconcile VLESS users and the loopback V2Ray API."""

    if not adapter_ready(11):
        raise RuntimeError("VLESS V2Ray API 适配器未就绪")
    try:
        config = json.loads(VLESS_CONFIG.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError("无法读取 sing-box 配置") from exc

    inbound = next(
        (
            item
            for item in config.get("inbounds", [])
            if item.get("tag") == "reality-in"
        ),
        None,
    )
    if not inbound or inbound.get("type") != "vless":
        raise RuntimeError("找不到 reality-in VLESS 入站")
    old_users = list(inbound.get("users", []))
    marker = adapter_marker()
    legacy_until = marker.get("VLESS_LEGACY_UNTIL", "")
    keep_legacy = False
    if legacy_until:
        try:
            keep_legacy = utcnow() < datetime.fromisoformat(
                legacy_until.replace("Z", "+00:00")
            )
        except ValueError:
            keep_legacy = False
    flow = next((u.get("flow", "") for u in old_users if u.get("flow") is not None), "")
    users = []
    if keep_legacy:
        users.extend(
            {k: user[k] for k in ("uuid", "flow") if k in user}
            for user in old_users
            # Named users are managed below from the database.  Only the
            # original unnamed shared credential belongs to the compatibility
            # window; preserving named users here would duplicate them after
            # every sync.
            if user.get("uuid") and not user.get("name")
        )
    for row in _live_vless_rows(db, extra=extra):
        values = credential_values(row)
        users.append(
            {"name": credential_stats_id(row), "uuid": values["uuid"], "flow": flow}
        )
    if not users:
        raise RuntimeError("VLESS 配置不能没有用户")
    inbound["users"] = users

    experimental = config.setdefault("experimental", {})
    experimental["v2ray_api"] = {
        "listen": "127.0.0.1:10085",
        "stats": {
            "enabled": True,
            "inbounds": ["reality-in"],
            "users": [u["name"] for u in users if u.get("name")],
        },
    }

    fd, temp_name = tempfile.mkstemp(
        prefix="config.", suffix=".json", dir=str(VLESS_CONFIG.parent)
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(config, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.chmod(temp_path, 0o600)
        _run_check(temp_path)
        os.replace(temp_path, VLESS_CONFIG)
    finally:
        temp_path.unlink(missing_ok=True)
    restarted = subprocess.run(
        ["systemctl", "restart", VLESS_SERVICE],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if restarted.returncode:
        raise RuntimeError("sing-box 重启失败")


def activate_credential(db, row: UserNodeCredential) -> UserNodeCredential:
    if row.node_id in REMOTE_AGENT_NODES:
        row.status = "pending"
        row.last_error = "等待节点 Agent 应用期望配置"
        row.last_synced_at = None
        return row
    if not adapter_ready(row.node_id, db):
        row.status = "error"
        row.last_error = "节点适配器未就绪"
        row.last_synced_at = None
        return row
    if row.node_id == 11:
        row.status = "active"
        row.last_error = ""
        db.flush()
        try:
            sync_vless_config(db, extra=row)
        except Exception as exc:
            row.status = "error"
            row.last_error = str(exc)[:1000]
            return row
    else:
        row.status = "active"
        row.last_error = ""
    row.last_synced_at = utcnow()
    return row
