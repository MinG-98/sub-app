import hashlib
import hmac
import json
import os
import re
import secrets
from datetime import timedelta, timezone
from pathlib import Path
from urllib.parse import quote

from fastapi import Body, Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from itsdangerous import BadSignature, URLSafeTimedSerializer
from sqlalchemy import func, select

from app.converter import parse_uri, render
from app.credentials import (
    credential_public_dict,
    ensure_credential,
    per_user_feature_enabled,
    render_credential_uri,
)
from app.latency import read_status, start_probe
from app.models import (
    Allocation,
    CollectorRun,
    Device,
    FetchLog,
    FlowRecord,
    Friend,
    Node,
    NodeAgent,
    NodeMetricSample,
    UserNodeCredential,
    make_session_factory,
    new_token,
    utcnow,
)
from app.proxy_adapters import (
    SUPPORTED_NODES,
    activate_credential,
    adapter_marker,
    adapter_ready,
    capability,
    node_per_user_enabled,
    sync_vless_config,
)
from app.traffic import (
    CollectorError,
    collector_config,
    fetch_servers,
    is_collector_configured,
)

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = os.environ.get("SUB_APP_DB", str(BASE_DIR / "data.db"))
ADMIN_PASSWORD = os.environ.get("SUB_APP_ADMIN_PASSWORD", "")
SECRET_KEY = os.environ.get("SUB_APP_SECRET", "")
PUBLIC_BASE = os.environ.get("SUB_APP_PUBLIC_BASE", "").rstrip("/")
SESSION_MAX_AGE = 7 * 24 * 3600
COOKIE_NAME = "sub_app_session"

if not ADMIN_PASSWORD:
    raise RuntimeError("SUB_APP_ADMIN_PASSWORD is required")
if not SECRET_KEY:
    raise RuntimeError("SUB_APP_SECRET is required")

Session = make_session_factory(DB_PATH)
serializer = URLSafeTimedSerializer(SECRET_KEY, salt="sub-app-session")

app = FastAPI(title="Sub App", docs_url=None, redoc_url=None, openapi_url=None)


def get_db():
    db = Session()
    try:
        yield db
    finally:
        db.close()


def require_admin(request: Request):
    raw = request.cookies.get(COOKIE_NAME)
    if not raw:
        raise HTTPException(status_code=401, detail="未登录")
    try:
        serializer.loads(raw, max_age=SESSION_MAX_AGE)
    except BadSignature:
        raise HTTPException(status_code=401, detail="登录已失效")
    return True


def client_ip(request: Request) -> str:
    fwd = request.headers.get("cf-connecting-ip") or request.headers.get(
        "x-forwarded-for", ""
    )
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else ""


def fingerprint_of(request: Request, supplied: str | None) -> str:
    if supplied:
        return hashlib.sha256(supplied.encode()).hexdigest()[:32]
    basis = f"{request.headers.get('user-agent','')}|{client_ip(request)}"
    return hashlib.sha256(basis.encode()).hexdigest()[:32]


def device_token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _is_legacy_device_value(value: str) -> bool:
    return bool(re.fullmatch(r"[0-9a-f]{32}", value or ""))


def _public_base(request: Request | None = None) -> str:
    if PUBLIC_BASE:
        return PUBLIC_BASE
    if request is None:
        return ""
    forwarded = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("host", request.url.netloc)
    return f"{forwarded.split(',')[0].strip()}://{host}"


def _device_dict(dev: Device, uid: str | None = None) -> dict:
    return {
        "id": dev.id,
        "friend_id": dev.friend_id,
        "friend_uid": uid,
        "access_identifier": dev.fingerprint,
        "fingerprint": dev.fingerprint,
        "identity_source": dev.identity_source or "legacy_ua_ip",
        "device_link_active": bool(
            dev.device_token_hash and dev.device_token_revoked_at is None
        ),
        "label": dev.label,
        "user_agent": dev.user_agent,
        "last_ip": dev.last_ip,
        "fetch_count": dev.fetch_count,
        "blocked": dev.blocked,
        "first_seen": _iso(dev.first_seen),
        "last_seen": _iso(dev.last_seen),
        "device_token_created_at": _iso(dev.device_token_created_at),
        "device_token_revoked_at": _iso(dev.device_token_revoked_at),
    }


def _credential_rows_for_friend(db, friend_id: int):
    return db.scalars(
        select(UserNodeCredential)
        .where(UserNodeCredential.friend_id == friend_id)
        .order_by(UserNodeCredential.node_id, UserNodeCredential.version.desc())
    ).all()


def _revoke_credential(db, row: UserNodeCredential):
    row.status = "revoked"
    row.revoked_at = utcnow()
    row.grace_until = None
    row.last_error = ""


def _reconcile_friend_credentials(db, friend: Friend):
    """Reconcile selected US nodes without affecting shared remote nodes."""
    allocations = db.scalars(
        select(Allocation).where(Allocation.friend_id == friend.id)
    ).all()
    wanted = {allocation.node_id for allocation in allocations}
    rows = _credential_rows_for_friend(db, friend.id)
    changed_vless = False
    if not friend.per_user_credentials or not friend.enabled:
        for row in rows:
            if row.revoked_at is None:
                _revoke_credential(db, row)
                changed_vless = changed_vless or row.node_id == 11
        if changed_vless and adapter_ready(11, db):
            try:
                sync_vless_config(db)
            except Exception as exc:
                for row in rows:
                    if row.node_id == 11 and row.status == "revoked":
                        row.last_error = str(exc)[:1000]
        return rows

    for node_id in wanted:
        node = db.get(Node, node_id)
        if (
            not node
            or node_id not in SUPPORTED_NODES
            or not node_per_user_enabled(node)
        ):
            continue
        if node.protocol != SUPPORTED_NODES[node_id]["protocol"]:
            continue
        row = ensure_credential(db, friend, node)
        if row.status not in {"active", "grace"} or row.revoked_at is not None:
            activate_credential(db, row)
            changed_vless = changed_vless or node_id == 11

    for row in rows:
        if row.node_id not in wanted and row.revoked_at is None:
            _revoke_credential(db, row)
            changed_vless = changed_vless or row.node_id == 11

    if changed_vless and adapter_ready(11, db):
        try:
            sync_vless_config(db)
        except Exception as exc:
            for row in _credential_rows_for_friend(db, friend.id):
                if row.node_id == 11 and row.status in {"active", "grace"}:
                    row.status = "error"
                    row.last_error = str(exc)[:1000]


# ---------------------------------------------------------------- auth


@app.post("/api/admin/login")
def login(response: Response, payload: dict = Body(...)):
    supplied = str(payload.get("password", ""))
    if not hmac.compare_digest(supplied, ADMIN_PASSWORD):
        raise HTTPException(status_code=401, detail="密码错误")
    token = serializer.dumps({"ok": True, "ts": utcnow().isoformat()})
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=True,
    )
    return {"ok": True}


@app.post("/api/admin/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE_NAME)
    return {"ok": True}


@app.get("/api/admin/me")
def me(request: Request):
    try:
        require_admin(request)
        return {"authenticated": True}
    except HTTPException:
        return {"authenticated": False}


# ---------------------------------------------------------------- nodes


def _iso(value):
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def _latest_metric(db, node_id):
    if db is None:
        return None
    return db.scalar(
        select(NodeMetricSample)
        .where(NodeMetricSample.node_id == node_id)
        .order_by(NodeMetricSample.bucket.desc())
        .limit(1)
    )


def _metric_dict(sample):
    if sample is None:
        return {
            "mapped": False,
            "online": None,
            "last_active": None,
            "last_heartbeat": None,
            "sampled_at": None,
            "collector_delay_seconds": None,
            "net_in_transfer": None,
            "net_out_transfer": None,
            "net_in_speed": None,
            "net_out_speed": None,
            "delta_in": 0,
            "delta_out": 0,
        }
    collected_at = sample.collected_at
    if collected_at is not None and collected_at.tzinfo is None:
        collected_at = collected_at.replace(tzinfo=timezone.utc)
    delay = None
    if collected_at is not None:
        delay = max(0, int((utcnow() - collected_at).total_seconds()))
    return {
        "mapped": True,
        "online": sample.online,
        "last_active": _iso(sample.last_active),
        "last_heartbeat": _iso(sample.last_active),
        "sampled_at": _iso(sample.collected_at),
        "collector_delay_seconds": delay,
        "net_in_transfer": sample.net_in_transfer,
        "net_out_transfer": sample.net_out_transfer,
        "net_in_speed": sample.net_in_speed,
        "net_out_speed": sample.net_out_speed,
        "delta_in": sample.delta_in,
        "delta_out": sample.delta_out,
    }


def _node_traffic_summary(db, node_id):
    if db is None:
        return {
            key: {"bytes_in": 0, "bytes_out": 0, "total": 0}
            for key in ("24h", "7d", "30d")
        }
    now = utcnow().replace(tzinfo=None)
    result = {}
    for key, days in (("24h", 1), ("7d", 7), ("30d", 30)):
        since = now - timedelta(days=days)
        bytes_in, bytes_out = db.execute(
            select(
                func.coalesce(func.sum(NodeMetricSample.delta_in), 0),
                func.coalesce(func.sum(NodeMetricSample.delta_out), 0),
            ).where(
                NodeMetricSample.node_id == node_id,
                NodeMetricSample.bucket >= since,
            )
        ).one()
        bytes_in = int(bytes_in or 0)
        bytes_out = int(bytes_out or 0)
        result[key] = {
            "bytes_in": bytes_in,
            "bytes_out": bytes_out,
            "total": bytes_in + bytes_out,
        }
    return result


def node_dict(n: Node, alloc_count: int = 0, db=None):
    parsed = parse_uri(n.uri) or {}
    metric = _metric_dict(_latest_metric(db, n.id))
    metric["mapped"] = n.nezha_server_id is not None
    adapter = capability(n, db)
    return {
        "id": n.id,
        "name": n.name,
        "protocol": n.protocol,
        "server": n.server,
        "port": parsed.get("port", 0),
        "enabled": n.enabled,
        "sort_order": n.sort_order,
        "nezha_server_id": n.nezha_server_id,
        "per_user_enabled": bool(n.per_user_enabled or adapter["ready"]),
        "per_user_capability": adapter,
        "allocated_to": alloc_count,
        "collector": metric,
        "traffic": _node_traffic_summary(db, n.id),
    }


@app.get("/api/admin/nodes")
def list_nodes(request: Request, db=Depends(get_db), _=Depends(require_admin)):
    counts = dict(
        db.execute(
            select(Allocation.node_id, func.count(Allocation.id)).group_by(
                Allocation.node_id
            )
        ).all()
    )
    nodes = db.scalars(select(Node).order_by(Node.sort_order, Node.id)).all()
    return [node_dict(n, counts.get(n.id, 0), db) for n in nodes]


@app.get("/api/admin/nodes/{node_id}/traffic")
def node_traffic(
    node_id: int,
    range: str = "24h",
    db=Depends(get_db),
    _=Depends(require_admin),
):
    node = db.get(Node, node_id)
    if not node:
        raise HTTPException(404, "节点不存在")
    days = {"24h": 1, "7d": 7, "30d": 30}.get(range)
    if days is None:
        raise HTTPException(400, "range 只能是 24h、7d 或 30d")
    since = utcnow() - timedelta(days=days)
    rows = (
        db.execute(
            select(NodeMetricSample)
            .where(
                NodeMetricSample.node_id == node_id,
                NodeMetricSample.bucket >= since.replace(tzinfo=None),
            )
            .order_by(NodeMetricSample.bucket)
        )
        .scalars()
        .all()
    )
    total_in = sum(row.delta_in for row in rows)
    total_out = sum(row.delta_out for row in rows)
    return {
        "node": node_dict(node, db=db),
        "range": range,
        "totals": {"bytes_in": total_in, "bytes_out": total_out},
        "points": [
            {
                "at": _iso(row.bucket),
                "online": row.online,
                "bytes_in": row.delta_in,
                "bytes_out": row.delta_out,
                "net_in_transfer": row.net_in_transfer,
                "net_out_transfer": row.net_out_transfer,
                "net_in_speed": row.net_in_speed,
                "net_out_speed": row.net_out_speed,
            }
            for row in rows
        ],
    }


def _topology_agent_state(agent):
    if agent is None:
        return {
            "configured": False,
            "status": "unconfigured",
            "online": False,
            "last_seen": None,
        }
    last_seen = agent.last_seen
    if last_seen is not None and last_seen.tzinfo is None:
        last_seen = last_seen.replace(tzinfo=timezone.utc)
    online = bool(
        last_seen is not None and (utcnow() - last_seen).total_seconds() <= 180
    )
    return {
        "configured": True,
        "status": agent.status,
        "online": online,
        "last_seen": _iso(agent.last_seen),
    }


def _topology_node_is_real(node: Node, parsed: dict) -> bool:
    """Keep placeholders out of the management graph without guessing routes."""
    if (node.name or "").startswith("⚠️"):
        return False
    host = parsed.get("host") or node.server or ""
    port = int(parsed.get("port") or 0)
    if node.protocol.lower() == "vless" and host == "127.0.0.1":
        return not (port <= 3 or port >= 40000)
    return True


@app.get("/api/admin/overview/topology")
def overview_topology(db=Depends(get_db), _=Depends(require_admin)):
    """Build the management graph from current allocations and node endpoints."""
    friends = db.scalars(select(Friend).order_by(Friend.id)).all()
    nodes = db.scalars(select(Node).order_by(Node.sort_order, Node.id)).all()
    allocations = db.execute(
        select(Allocation.friend_id, Allocation.node_id).order_by(
            Allocation.friend_id, Allocation.node_id
        )
    ).all()
    agents = {agent.node_id: agent for agent in db.scalars(select(NodeAgent)).all()}
    by_friend = {}
    for friend_id, node_id in allocations:
        by_friend.setdefault(friend_id, []).append(node_id)
    nodes_by_id = {node.id: node for node in nodes}
    graph_nodes = {}
    edges = []
    seen_relations = set()

    for friend in friends:
        friend_name = f"用户/{friend.uid}"
        graph_nodes.setdefault(
            friend_name,
            {
                "name": friend_name,
                "type": "user",
                "friend_id": friend.id,
                "enabled": bool(friend.enabled),
                "node_count": 0,
            },
        )
        for node_id in by_friend.get(friend.id, []):
            node = nodes_by_id.get(node_id)
            if node is None:
                continue
            parsed = parse_uri(node.uri) or {}
            if not _topology_node_is_real(node, parsed):
                continue
            node_name = f"节点/{node.name}"
            host = parsed.get("host") or node.server or "未知"
            server_name = f"服务器/{host}"
            agent_state = _topology_agent_state(agents.get(node.id))
            graph_nodes.setdefault(
                node_name,
                {
                    "name": node_name,
                    "type": "node",
                    "node_id": node.id,
                    "protocol": node.protocol,
                    "enabled": bool(node.enabled),
                    "agent": agent_state,
                },
            )
            graph_nodes.setdefault(
                server_name,
                {
                    "name": server_name,
                    "type": "server",
                    "server": host,
                    "node_ids": [],
                },
            )
            graph_nodes[friend_name]["node_count"] += 1
            graph_nodes[server_name]["node_ids"].append(node.id)
            relation = (friend_name, node_name)
            if relation not in seen_relations:
                edges.append(
                    {
                        "source": friend_name,
                        "target": node_name,
                        "value": 1,
                        "kind": "allocation",
                        "active": bool(friend.enabled and node.enabled),
                    }
                )
                seen_relations.add(relation)
            relation = (node_name, server_name)
            if relation not in seen_relations:
                edges.append(
                    {
                        "source": node_name,
                        "target": server_name,
                        "value": 1,
                        "kind": "endpoint",
                        "active": bool(node.enabled),
                    }
                )
                seen_relations.add(relation)

    return {
        "mode": "management",
        "source": "database_allocations",
        "generated_at": _iso(utcnow()),
        "summary": {
            "users": sum(1 for item in graph_nodes.values() if item["type"] == "user"),
            "nodes": sum(1 for item in graph_nodes.values() if item["type"] == "node"),
            "servers": sum(
                1 for item in graph_nodes.values() if item["type"] == "server"
            ),
            "relations": sum(1 for edge in edges if edge["kind"] == "allocation"),
        },
        "nodes": list(graph_nodes.values()),
        "edges": edges,
    }


def _collector_status(db):
    proxy_status_path = Path(
        os.environ.get(
            "SUB_APP_PROXY_STATUS", "/var/lib/sub-app/proxy-collector-status.json"
        )
    )
    reconciler_status_path = Path(
        os.environ.get(
            "SUB_APP_RECONCILER_STATUS", "/var/lib/sub-app/reconciler-status.json"
        )
    )
    try:
        proxy_status = json.loads(proxy_status_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        proxy_status = {"status": "never_run"}
    try:
        reconciler_status = json.loads(
            reconciler_status_path.read_text(encoding="utf-8")
        )
    except (FileNotFoundError, ValueError):
        reconciler_status = {"status": "never_run"}
    run = db.scalar(
        select(CollectorRun)
        .where(CollectorRun.source == "nezha")
        .order_by(CollectorRun.started_at.desc())
        .limit(1)
    )
    last_success = db.scalar(
        select(CollectorRun)
        .where(
            CollectorRun.source == "nezha",
            CollectorRun.status == "success",
        )
        .order_by(CollectorRun.finished_at.desc())
        .limit(1)
    )
    if run is None:
        return {
            "configured": is_collector_configured(),
            "status": "never_run",
            "started_at": None,
            "finished_at": None,
            "last_success_at": None,
            "nodes_total": 0,
            "samples_written": 0,
            "error": "",
            "proxy": proxy_status,
            "reconciler": reconciler_status,
        }
    return {
        "configured": is_collector_configured(),
        "status": run.status,
        "started_at": _iso(run.started_at),
        "finished_at": _iso(run.finished_at),
        "last_success_at": _iso(last_success.finished_at) if last_success else None,
        "nodes_total": run.nodes_total,
        "samples_written": run.samples_written,
        "error": run.error or "",
        "proxy": proxy_status,
        "reconciler": reconciler_status,
    }


@app.get("/api/admin/collector/status")
def collector_status(db=Depends(get_db), _=Depends(require_admin)):
    return _collector_status(db)


@app.get("/api/admin/latency")
def latency_status(_=Depends(require_admin)):
    """Return the last real node-entry/proxy-exit probe without secrets."""
    return read_status()


@app.post("/api/admin/latency/probe")
def trigger_latency_probe(_=Depends(require_admin)):
    """Start a bounded background probe; repeated clicks are coalesced."""
    return start_probe()


@app.get("/api/admin/collector/servers")
def collector_servers(_=Depends(require_admin)):
    base_url, token = collector_config()
    try:
        servers = fetch_servers(base_url, token)
    except CollectorError as exc:
        raise HTTPException(503, str(exc))
    return [
        {
            "id": server["id"],
            "name": server["name"],
            "online": server["online"],
            "last_active": _iso(server["last_active"]),
        }
        for server in servers
    ]


@app.post("/api/admin/nodes")
def create_nodes(
    payload: dict = Body(...), db=Depends(get_db), _=Depends(require_admin)
):
    """Accepts either a single node or a bulk paste of URIs."""
    bulk = payload.get("bulk", "")
    created, skipped = [], []

    raw_list = (
        [line.strip() for line in bulk.splitlines() if line.strip()] if bulk else []
    )
    if payload.get("uri"):
        raw_list.append(payload["uri"].strip())

    max_order = db.scalar(select(func.max(Node.sort_order))) or 0

    for raw in raw_list:
        parsed = parse_uri(raw)
        if not parsed:
            skipped.append(raw[:60])
            continue
        exists = db.scalar(select(Node).where(Node.uri == raw))
        if exists:
            skipped.append(f"重复: {parsed.get('name') or raw[:40]}")
            continue
        max_order += 1
        node = Node(
            name=payload.get("name")
            or parsed["name"]
            or f"{parsed['scheme']}-{parsed['host']}",
            protocol=parsed["scheme"],
            uri=raw,
            server=parsed["host"],
            sort_order=max_order,
        )
        db.add(node)
        created.append(node.name)

    db.commit()
    return {"created": created, "skipped": skipped}


@app.patch("/api/admin/nodes/{node_id}")
def update_node(
    node_id: int,
    payload: dict = Body(...),
    db=Depends(get_db),
    _=Depends(require_admin),
):
    node = db.get(Node, node_id)
    if not node:
        raise HTTPException(404, "节点不存在")
    if payload.get("per_user_enabled") and not adapter_ready(node_id, db):
        raise HTTPException(409, "该节点的代理适配器尚未就绪")
    for field in (
        "name",
        "enabled",
        "sort_order",
        "nezha_server_id",
        "per_user_enabled",
    ):
        if field in payload:
            setattr(node, field, payload[field])
    if payload.get("uri"):
        parsed = parse_uri(payload["uri"])
        if not parsed:
            raise HTTPException(400, "无法解析该节点链接")
        node.uri = payload["uri"]
        node.protocol = parsed["scheme"]
        node.server = parsed["host"]
    db.commit()
    return node_dict(node, db=db)


@app.post("/api/admin/nodes/{node_id}/per-user/prepare")
def prepare_node_credentials(
    node_id: int, db=Depends(get_db), _=Depends(require_admin)
):
    """Prepare/sync opted-in users for a meterable node."""
    node = db.get(Node, node_id)
    if not node:
        raise HTTPException(404, "节点不存在")
    if node.protocol not in ("vless", "hysteria2"):
        raise HTTPException(400, "首批独立凭据只支持 VLESS 和 Hysteria2")
    friends = db.scalars(
        select(Friend)
        .join(Allocation, Allocation.friend_id == Friend.id)
        .where(Allocation.node_id == node_id, Friend.enabled.is_(True))
        .order_by(Friend.id)
    ).all()
    rows = []
    for friend in friends:
        if not friend.per_user_credentials:
            continue
        row = ensure_credential(db, friend, node)
        activate_credential(db, row)
        rows.append(row)
    db.commit()
    return {
        "ok": True,
        "node_id": node_id,
        "feature_enabled": per_user_feature_enabled(),
        "activated": bool(rows) and all(row.status == "active" for row in rows),
        "reason": "adapter sync attempted; failures remain pending/error",
        "credentials": [credential_public_dict(row) for row in rows],
    }


@app.delete("/api/admin/nodes/{node_id}")
def delete_node(node_id: int, db=Depends(get_db), _=Depends(require_admin)):
    node = db.get(Node, node_id)
    if not node:
        raise HTTPException(404, "节点不存在")
    db.delete(node)
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- friends


def _friend_usage(db, friend_id, since=None):
    query = select(
        func.coalesce(func.sum(FlowRecord.bytes_in), 0),
        func.coalesce(func.sum(FlowRecord.bytes_out), 0),
    ).where(
        FlowRecord.friend_id == friend_id,
        FlowRecord.source != "nezha",
    )
    if since is not None:
        query = query.where(FlowRecord.bucket >= since)
    bytes_in, bytes_out = db.execute(query).one()
    return {"bytes_in": int(bytes_in or 0), "bytes_out": int(bytes_out or 0)}


def friend_dict(
    f: Friend, node_ids: list[int], device_count: int = 0, usage=None, db=None
):
    base = PUBLIC_BASE or ""
    usage = usage or (
        _friend_usage(db, f.id) if db is not None else {"bytes_in": 0, "bytes_out": 0}
    )
    used = usage["bytes_in"] + usage["bytes_out"]
    limit = f.flow_limit_gb * 1024**3
    percent = (used / limit * 100) if limit else 0
    alert = (
        "over"
        if limit and used >= limit
        else ("warning" if limit and used >= limit * 0.8 else "ok")
    )
    credential_status = []
    if db is not None:
        credential_status = [
            credential_public_dict(row) for row in _credential_rows_for_friend(db, f.id)
        ]
    unsupported = [node_id for node_id in node_ids if node_id not in SUPPORTED_NODES]
    return {
        "id": f.id,
        "uid": f.uid,
        "remark": f.remark,
        "token": f.token,
        "enabled": f.enabled,
        "flow_limit_gb": f.flow_limit_gb,
        "device_limit": f.device_limit,
        "per_user_credentials": f.per_user_credentials,
        "credential_status": credential_status,
        "unsupported_node_ids": unsupported,
        "node_ids": node_ids,
        "device_count": device_count,
        "flow_used_bytes": used,
        "flow_in_bytes": usage["bytes_in"],
        "flow_out_bytes": usage["bytes_out"],
        "flow_limit_bytes": limit,
        "flow_percent": round(percent, 2),
        "flow_alert": alert,
        "created_at": f.created_at.isoformat(),
        "links": {
            "clash": f"{base}/sub/{f.token}?target=clash",
            "v2ray": f"{base}/sub/{f.token}?target=v2ray",
        },
    }


@app.get("/api/admin/friends")
def list_friends(db=Depends(get_db), _=Depends(require_admin)):
    friends = db.scalars(select(Friend).order_by(Friend.id)).all()
    allocs = db.execute(select(Allocation.friend_id, Allocation.node_id)).all()
    by_friend: dict[int, list[int]] = {}
    for fid, nid in allocs:
        by_friend.setdefault(fid, []).append(nid)
    dev_counts = dict(
        db.execute(
            select(Device.friend_id, func.count(Device.id)).group_by(Device.friend_id)
        ).all()
    )
    return [
        friend_dict(
            f,
            by_friend.get(f.id, []),
            dev_counts.get(f.id, 0),
            db=db,
        )
        for f in friends
    ]


@app.post("/api/admin/friends")
def create_friend(
    payload: dict = Body(...), db=Depends(get_db), _=Depends(require_admin)
):
    uid = (payload.get("uid") or "").strip()
    if not uid:
        raise HTTPException(400, "UID 不能为空")
    if db.scalar(select(Friend).where(Friend.uid == uid)):
        raise HTTPException(400, "该 UID 已存在")
    friend = Friend(
        uid=uid,
        remark=payload.get("remark", ""),
        flow_limit_gb=int(payload.get("flow_limit_gb", 0) or 0),
        device_limit=int(payload.get("device_limit", 0) or 0),
        per_user_credentials=bool(payload.get("per_user_credentials", False)),
        token=new_token(),
    )
    db.add(friend)
    db.flush()
    node_ids = [int(nid) for nid in payload.get("node_ids", [])]
    for nid in node_ids:
        if db.get(Node, nid):
            db.add(Allocation(friend_id=friend.id, node_id=nid))
    db.flush()
    _reconcile_friend_credentials(db, friend)
    db.commit()
    return friend_dict(friend, node_ids, db=db)


@app.patch("/api/admin/friends/{friend_id}")
def update_friend(
    friend_id: int,
    payload: dict = Body(...),
    db=Depends(get_db),
    _=Depends(require_admin),
):
    friend = db.get(Friend, friend_id)
    if not friend:
        raise HTTPException(404, "用户不存在")

    for field in (
        "remark",
        "enabled",
        "flow_limit_gb",
        "device_limit",
        "per_user_credentials",
    ):
        if field in payload:
            setattr(friend, field, payload[field])

    if "node_ids" in payload:
        wanted = {int(n) for n in payload["node_ids"]}
        current = {
            a.node_id: a
            for a in db.scalars(
                select(Allocation).where(Allocation.friend_id == friend_id)
            ).all()
        }
        for nid in wanted - current.keys():
            db.add(Allocation(friend_id=friend_id, node_id=nid))
        for nid in current.keys() - wanted:
            db.delete(current[nid])

    if payload.get("rotate_token"):
        friend.token = new_token()

    db.flush()
    _reconcile_friend_credentials(db, friend)
    db.commit()
    node_ids = [
        a.node_id
        for a in db.scalars(
            select(Allocation).where(Allocation.friend_id == friend_id)
        ).all()
    ]
    return friend_dict(friend, node_ids, db=db)


@app.delete("/api/admin/friends/{friend_id}")
def delete_friend(friend_id: int, db=Depends(get_db), _=Depends(require_admin)):
    friend = db.get(Friend, friend_id)
    if not friend:
        raise HTTPException(404, "用户不存在")
    db.delete(friend)
    db.commit()
    return {"ok": True}


@app.get("/api/admin/friends/{friend_id}/traffic")
def friend_traffic(
    friend_id: int,
    range: str = "24h",
    db=Depends(get_db),
    _=Depends(require_admin),
):
    friend = db.get(Friend, friend_id)
    if not friend:
        raise HTTPException(404, "用户不存在")
    days = {"24h": 1, "7d": 7, "30d": 30}.get(range)
    if days is None:
        raise HTTPException(400, "range 只能是 24h、7d 或 30d")
    since = (utcnow() - timedelta(days=days)).replace(tzinfo=None)
    rows = db.execute(
        select(
            FlowRecord.bucket,
            func.coalesce(func.sum(FlowRecord.bytes_in), 0),
            func.coalesce(func.sum(FlowRecord.bytes_out), 0),
        )
        .where(
            FlowRecord.friend_id == friend_id,
            FlowRecord.source != "nezha",
            FlowRecord.bucket >= since,
        )
        .group_by(FlowRecord.bucket)
        .order_by(FlowRecord.bucket)
    ).all()
    return {
        "friend_id": friend_id,
        "range": range,
        "totals": {
            "bytes_in": sum(int(row[1]) for row in rows),
            "bytes_out": sum(int(row[2]) for row in rows),
        },
        "points": [
            {"at": _iso(row[0]), "bytes_in": int(row[1]), "bytes_out": int(row[2])}
            for row in rows
        ],
    }


@app.get("/api/admin/friends/{friend_id}/credentials")
def friend_credentials(friend_id: int, db=Depends(get_db), _=Depends(require_admin)):
    if not db.get(Friend, friend_id):
        raise HTTPException(404, "用户不存在")
    rows = db.scalars(
        select(UserNodeCredential)
        .where(UserNodeCredential.friend_id == friend_id)
        .order_by(UserNodeCredential.node_id, UserNodeCredential.version.desc())
    ).all()
    return [
        {
            "id": row.id,
            "node_id": row.node_id,
            "protocol": row.protocol,
            "credential_name": row.credential_name,
            "version": row.version,
            "status": row.status,
            "created_at": _iso(row.created_at),
            "rotated_at": _iso(row.rotated_at),
            "revoked_at": _iso(row.revoked_at),
            "grace_until": _iso(row.grace_until),
            "last_synced_at": _iso(row.last_synced_at),
            "last_error": row.last_error or "",
        }
        for row in rows
    ]


@app.post("/api/admin/credentials/{credential_id}/sync")
def sync_credential(credential_id: int, db=Depends(get_db), _=Depends(require_admin)):
    row = db.get(UserNodeCredential, credential_id)
    if not row:
        raise HTTPException(404, "凭据不存在")
    friend = db.get(Friend, row.friend_id)
    if not friend or not friend.enabled or not friend.per_user_credentials:
        raise HTTPException(409, "用户未启用独立凭据")
    activate_credential(db, row)
    db.commit()
    return credential_public_dict(row)


@app.post("/api/admin/credentials/{credential_id}/rotate")
def rotate_credential(credential_id: int, db=Depends(get_db), _=Depends(require_admin)):
    old = db.get(UserNodeCredential, credential_id)
    if not old:
        raise HTTPException(404, "凭据不存在")
    friend = db.get(Friend, old.friend_id)
    node = db.get(Node, old.node_id)
    if not friend or not node:
        raise HTTPException(404, "用户或节点不存在")
    if not friend.enabled or not friend.per_user_credentials:
        raise HTTPException(409, "用户未启用独立凭据")
    latest = db.scalar(
        select(UserNodeCredential)
        .where(
            UserNodeCredential.friend_id == old.friend_id,
            UserNodeCredential.node_id == old.node_id,
            UserNodeCredential.protocol == old.protocol,
        )
        .order_by(UserNodeCredential.version.desc())
        .limit(1)
    )
    now = utcnow()
    if old.revoked_at is None:
        old.status = "grace"
        old.grace_until = now + timedelta(hours=24)
        old.rotated_at = now
    row = UserNodeCredential(
        friend_id=old.friend_id,
        node_id=old.node_id,
        protocol=old.protocol,
        credential_name=f"u{old.friend_id}n{old.node_id}",
        version=(latest.version + 1) if latest else 1,
        status="pending",
        created_at=now,
    )
    db.add(row)
    db.flush()
    activate_credential(db, row)
    if row.status != "active" and old.revoked_at is None:
        old.status = "active"
        old.grace_until = None
        old.last_error = row.last_error
    db.commit()
    return {
        "credential": credential_public_dict(row),
        "previous": credential_public_dict(old),
    }


@app.post("/api/admin/credentials/{credential_id}/revoke")
def revoke_credential(credential_id: int, db=Depends(get_db), _=Depends(require_admin)):
    row = db.get(UserNodeCredential, credential_id)
    if not row:
        raise HTTPException(404, "凭据不存在")
    _revoke_credential(db, row)
    if row.node_id == 11 and adapter_ready(11, db):
        try:
            sync_vless_config(db)
        except Exception as exc:
            row.last_error = str(exc)[:1000]
    db.commit()
    return credential_public_dict(row)


# ---------------------------------------------------------------- devices


@app.get("/api/admin/devices")
def list_devices(db=Depends(get_db), _=Depends(require_admin)):
    rows = db.execute(
        select(Device, Friend.uid).join(Friend, Device.friend_id == Friend.id)
    ).all()
    out = []
    for dev, uid in rows:
        out.append(_device_dict(dev, uid))
    out.sort(key=lambda d: d["last_seen"], reverse=True)
    return out


def _device_links(request: Request, friend: Friend, raw_token: str) -> dict:
    base = _public_base(request)
    encoded = quote(raw_token, safe="")
    return {
        "clash": f"{base}/sub/{friend.token}?device={encoded}&target=clash",
        "v2ray": f"{base}/sub/{friend.token}?device={encoded}&target=v2ray",
    }


@app.post("/api/admin/friends/{friend_id}/devices")
def create_device_link(
    friend_id: int,
    request: Request,
    payload: dict = Body(default={}),
    db=Depends(get_db),
    _=Depends(require_admin),
):
    friend = db.get(Friend, friend_id)
    if not friend:
        raise HTTPException(404, "用户不存在")
    count = (
        db.scalar(select(func.count(Device.id)).where(Device.friend_id == friend_id))
        or 0
    )
    if friend.device_limit and count >= friend.device_limit:
        raise HTTPException(409, "设备数已达上限")
    raw_token = secrets.token_urlsafe(24)
    token_hash = device_token_hash(raw_token)
    now = utcnow()
    dev = Device(
        friend_id=friend_id,
        fingerprint=token_hash[:32],
        device_token_hash=token_hash,
        identity_source="device_link",
        device_token_created_at=now,
        label=str(payload.get("label", ""))[:128],
        first_seen=now,
        last_seen=now,
    )
    db.add(dev)
    db.commit()
    return {
        "ok": True,
        "device": _device_dict(dev, friend.uid),
        "links": _device_links(request, friend, raw_token),
        "notice": "链接只在本次响应中返回；数据库仅保存哈希",
    }


@app.post("/api/admin/devices/{device_id}/rotate-link")
def rotate_device_link(
    device_id: int,
    request: Request,
    db=Depends(get_db),
    _=Depends(require_admin),
):
    dev = db.get(Device, device_id)
    if not dev:
        raise HTTPException(404, "设备不存在")
    friend = db.get(Friend, dev.friend_id)
    raw_token = secrets.token_urlsafe(24)
    now = utcnow()
    dev.device_token_hash = device_token_hash(raw_token)
    dev.fingerprint = dev.device_token_hash[:32]
    dev.identity_source = "device_link"
    dev.device_token_created_at = now
    dev.device_token_revoked_at = None
    db.commit()
    return {
        "ok": True,
        "device": _device_dict(dev, friend.uid if friend else None),
        "links": _device_links(request, friend, raw_token) if friend else {},
        "notice": "旧链接已失效；新链接只在本次响应中返回",
    }


@app.post("/api/admin/devices/{device_id}/revoke-link")
def revoke_device_link(device_id: int, db=Depends(get_db), _=Depends(require_admin)):
    dev = db.get(Device, device_id)
    if not dev:
        raise HTTPException(404, "设备不存在")
    dev.device_token_revoked_at = utcnow()
    db.commit()
    return {"ok": True}


@app.patch("/api/admin/devices/{device_id}")
def update_device(
    device_id: int,
    payload: dict = Body(...),
    db=Depends(get_db),
    _=Depends(require_admin),
):
    dev = db.get(Device, device_id)
    if not dev:
        raise HTTPException(404, "设备不存在")
    for field in ("label", "blocked"):
        if field in payload:
            setattr(dev, field, payload[field])
    db.commit()
    uid = db.scalar(select(Friend.uid).where(Friend.id == dev.friend_id))
    return {"ok": True, "device": _device_dict(dev, uid)}


@app.delete("/api/admin/devices/{device_id}")
def delete_device(device_id: int, db=Depends(get_db), _=Depends(require_admin)):
    dev = db.get(Device, device_id)
    if not dev:
        raise HTTPException(404, "设备不存在")
    db.delete(dev)
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- stats


@app.get("/api/admin/stats")
def stats(db=Depends(get_db), _=Depends(require_admin)):
    now = utcnow()
    day_ago = now - timedelta(days=1)
    week_ago = now - timedelta(days=7)

    total_fetch_24h = db.scalar(
        select(func.count(FetchLog.id)).where(FetchLog.created_at >= day_ago)
    )
    active_devices_24h = db.scalar(
        select(func.count(func.distinct(Device.id))).where(Device.last_seen >= day_ago)
    )

    per_friend = db.execute(
        select(Friend.uid, func.count(FetchLog.id))
        .join(FetchLog, FetchLog.friend_id == Friend.id, isouter=True)
        .where((FetchLog.created_at >= week_ago) | (FetchLog.id.is_(None)))
        .group_by(Friend.uid)
    ).all()

    recent = db.execute(
        select(FetchLog, Friend.uid)
        .join(Friend, FetchLog.friend_id == Friend.id, isouter=True)
        .order_by(FetchLog.created_at.desc())
        .limit(30)
    ).all()

    return {
        "nodes": db.scalar(select(func.count(Node.id))),
        "friends": db.scalar(select(func.count(Friend.id))),
        "devices": db.scalar(select(func.count(Device.id))),
        "fetch_24h": total_fetch_24h,
        "active_devices_24h": active_devices_24h,
        "flow_24h_bytes": sum(
            _friend_usage(db, f.id, day_ago)["bytes_in"]
            + _friend_usage(db, f.id, day_ago)["bytes_out"]
            for f in db.scalars(select(Friend)).all()
        ),
        "collector": _collector_status(db),
        "per_friend_week": [{"uid": u, "fetches": c} for u, c in per_friend],
        "recent": [
            {
                "uid": uid,
                "target": log.target,
                "ip": log.ip,
                "user_agent": (log.user_agent or "")[:60],
                "at": log.created_at.isoformat(),
            }
            for log, uid in recent
        ],
    }


# ---------------------------------------------------------------- subscription


@app.get("/sub/{token}")
def subscription(
    token: str,
    request: Request,
    target: str = "v2ray",
    device: str | None = None,
    db=Depends(get_db),
):
    friend = db.scalar(select(Friend).where(Friend.token == token))
    if not friend or not friend.enabled:
        raise HTTPException(404, "订阅不存在或已停用")

    supplied_device = (device or "").strip()
    ua = request.headers.get("user-agent", "")[:250]
    ip = client_ip(request)
    device_link = None
    if supplied_device:
        device_link = db.scalar(
            select(Device).where(
                Device.friend_id == friend.id,
                Device.device_token_hash == device_token_hash(supplied_device),
            )
        )
        if device_link is None and not _is_legacy_device_value(supplied_device):
            raise HTTPException(404, "设备订阅链接无效")
    fp = (
        device_link.fingerprint
        if device_link
        else fingerprint_of(request, supplied_device or None)
    )
    dev = device_link or db.scalar(
        select(Device).where(Device.friend_id == friend.id, Device.fingerprint == fp)
    )
    if dev is None:
        existing = db.scalar(
            select(func.count(Device.id)).where(Device.friend_id == friend.id)
        )
        if friend.device_limit and existing >= friend.device_limit:
            raise HTTPException(403, "设备数已达上限")
        dev = Device(
            friend_id=friend.id,
            fingerprint=fp,
            identity_source="legacy_ua_ip",
            user_agent=ua,
            last_ip=ip,
            fetch_count=0,
        )
        db.add(dev)
        db.flush()

    if device_link and device_link.device_token_revoked_at is not None:
        raise HTTPException(403, "该设备链接已撤销")

    if dev.blocked:
        raise HTTPException(403, "该设备已被停用")

    dev.user_agent = ua
    dev.last_ip = ip
    dev.last_seen = utcnow()
    dev.fetch_count += 1

    db.add(
        FetchLog(
            friend_id=friend.id, device_id=dev.id, target=target, ip=ip, user_agent=ua
        )
    )

    uris = []
    allocated_nodes = db.scalars(
        select(Node)
        .join(Allocation, Allocation.node_id == Node.id)
        .where(Allocation.friend_id == friend.id, Node.enabled.is_(True))
        .order_by(Node.sort_order, Node.id)
    ).all()
    skipped_nodes = []
    for node in allocated_nodes:
        use_per_user = (
            friend.per_user_credentials
            and per_user_feature_enabled()
            and node.id in SUPPORTED_NODES
            and node.protocol == SUPPORTED_NODES[node.id]["protocol"]
            and node_per_user_enabled(node)
        )
        if not use_per_user:
            uris.append(node.uri)
            continue
        row = db.scalar(
            select(UserNodeCredential)
            .where(
                UserNodeCredential.friend_id == friend.id,
                UserNodeCredential.node_id == node.id,
                UserNodeCredential.revoked_at.is_(None),
                UserNodeCredential.status == "active",
            )
            .order_by(UserNodeCredential.version.desc())
            .limit(1)
        )
        if row is None:
            skipped_nodes.append(node.id)
            continue
        uris.append(render_credential_uri(node, row))
    db.commit()

    if not uris:
        raise HTTPException(404, "尚未分配任何节点")

    try:
        body, content_type = render(uris, target)
    except ValueError as exc:
        raise HTTPException(400, str(exc))

    headers = {
        "profile-update-interval": "12",
        "x-sub-app-skipped-nodes": str(len(skipped_nodes)),
        "content-disposition": (
            f'attachment; filename="{friend.uid}.yaml"'
            if target.startswith("clash")
            else f'attachment; filename="{friend.uid}.txt"'
        ),
    }
    return PlainTextResponse(body, media_type=content_type, headers=headers)


# ---------------------------------------------------------------- frontend

STATIC_DIR = BASE_DIR / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def index():
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.get("/healthz")
def healthz():
    db = Session()
    try:
        adapters = adapter_marker()
        proxy_status = {}
        status_path = Path(
            os.environ.get(
                "SUB_APP_PROXY_STATUS", "/var/lib/sub-app/proxy-collector-status.json"
            )
        )
        try:
            proxy_status = json.loads(status_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            proxy_status = {"status": "never_run"}
        collector_state = _collector_status(db)
        return {
            "ok": True,
            "generated_at": _iso(utcnow()),
            "database": "ok",
            "collector": collector_state,
            "proxy_collector": proxy_status,
            "reconciler": collector_state.get("reconciler", {"status": "never_run"}),
            "agents": agent_health_summary(db),
            "adapters": {
                "hysteria2": adapters.get("HY2_HTTP_AUTH") == "1",
                "vless": adapters.get("VLESS_V2RAY_API") == "1",
            },
        }
    finally:
        db.close()


# ---------------------------------------------------------------- node agents

from app.agent_api import agent_health_summary, register_agent_routes  # noqa: E402

register_agent_routes(app, Session, get_db, require_admin)
