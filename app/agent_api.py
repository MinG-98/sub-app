"""Authenticated control-plane endpoints for per-node agents.

Agents receive only the desired credentials for their own node.  Tokens are
stored as hashes in SQLite; request access logs are disabled on the service.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone

from fastapi import Body, Depends, HTTPException, Request
from sqlalchemy import insert, select

from app.credentials import credential_stats_id, credential_values
from app.models import FlowRecord, Friend, Node, NodeAgent, UserNodeCredential, utcnow
from app.proxy_adapters import (
    REMOTE_AGENT_NODES,
    SUPPORTED_NODES,
)

ACTIVE_CREDENTIAL_STATUSES = ("active", "grace")
APPLICABLE_CREDENTIAL_STATUSES = ("pending", "error", "active")


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def agent_public_dict(agent: NodeAgent | None) -> dict | None:
    if agent is None:
        return None
    try:
        capabilities = json.loads(agent.capabilities or "{}")
    except (TypeError, ValueError):
        capabilities = {}
    return {
        "id": agent.id,
        "node_id": agent.node_id,
        "status": agent.status,
        "agent_version": agent.agent_version,
        "capabilities": capabilities,
        "desired_generation": agent.desired_generation,
        "applied_generation": agent.applied_generation,
        "last_seen": agent.last_seen.isoformat() if agent.last_seen else None,
        "last_error": agent.last_error or "",
        "updated_at": agent.updated_at.isoformat() if agent.updated_at else None,
    }


def agent_health_summary(db) -> dict:
    now = utcnow().replace(tzinfo=None)
    rows = db.scalars(select(NodeAgent)).all()
    online = 0
    errors = 0
    for row in rows:
        last_seen = row.last_seen
        if last_seen and last_seen.tzinfo is not None:
            last_seen = last_seen.astimezone(timezone.utc).replace(tzinfo=None)
        if last_seen and (now - last_seen).total_seconds() <= 180:
            online += 1
        if row.status == "error":
            errors += 1
    return {
        "configured": bool(rows),
        "total": len(rows),
        "online": online,
        "errors": errors,
    }


def _authorization_token(request: Request) -> str:
    direct = request.headers.get("x-sub-app-agent-token", "").strip()
    if direct:
        return direct
    value = request.headers.get("authorization", "")
    if value.lower().startswith("bearer "):
        return value[7:].strip()
    return ""


def _require_agent(request: Request, db, node_id: int) -> NodeAgent:
    agent = db.scalar(select(NodeAgent).where(NodeAgent.node_id == node_id))
    supplied = _authorization_token(request)
    if (
        not agent
        or not supplied
        or not hmac.compare_digest(token_hash(supplied), agent.token_hash)
    ):
        raise HTTPException(status_code=401, detail="节点 Agent 未授权")
    return agent


def _utc_naive(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _safe_int(value, default=0) -> int:
    try:
        value = int(value)
    except (TypeError, ValueError):
        return default
    return max(0, min(value, 9_223_372_036_854_775_807))


def _desired_users(db, node_id: int) -> list[dict]:
    rows = db.execute(
        select(UserNodeCredential, Friend)
        .join(Friend, Friend.id == UserNodeCredential.friend_id)
        .where(
            UserNodeCredential.node_id == node_id,
            UserNodeCredential.revoked_at.is_(None),
            Friend.enabled.is_(True),
            Friend.per_user_credentials.is_(True),
        )
        .order_by(UserNodeCredential.friend_id, UserNodeCredential.version)
    ).all()
    result = []
    for row, friend in rows:
        values = credential_values(row)
        item = {
            "credential_id": row.id,
            "friend_id": row.friend_id,
            "uid": friend.uid,
            "protocol": row.protocol,
            "version": row.version,
            "status": row.status,
            "stats_id": credential_stats_id(row),
            "grace_until": row.grace_until.isoformat() if row.grace_until else None,
        }
        item.update(values)
        result.append(item)
    return result


def _desired_payload(db, node: Node, agent: NodeAgent) -> dict:
    # Local US adapters already have their own reconciler.  Remote nodes only
    # enter user mode after the administrator explicitly enables the node.
    apply_enabled = (
        node.id in REMOTE_AGENT_NODES
        and bool(node.per_user_enabled)
        and node.protocol == SUPPORTED_NODES.get(node.id, {}).get("protocol")
    )
    users = _desired_users(db, node.id) if apply_enabled else []
    unsigned = {
        "node_id": node.id,
        "protocol": node.protocol,
        "apply": apply_enabled,
        "users": users,
    }
    encoded = json.dumps(
        unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    generation = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    agent.desired_generation = generation
    agent.updated_at = utcnow()
    return {
        **unsigned,
        "generation": generation,
        "agent_id": agent.id,
        "poll_after_seconds": 30,
        "legacy_window_hours": 24,
    }


def _record_traffic(db, node_id: int, payload) -> int:
    if not isinstance(payload, list):
        return 0
    rows = db.scalars(
        select(UserNodeCredential)
        .join(Friend, Friend.id == UserNodeCredential.friend_id)
        .where(
            UserNodeCredential.node_id == node_id,
            UserNodeCredential.revoked_at.is_(None),
            UserNodeCredential.status.in_(ACTIVE_CREDENTIAL_STATUSES),
            Friend.enabled.is_(True),
            Friend.per_user_credentials.is_(True),
        )
    ).all()
    by_key = {credential_stats_id(row): row for row in rows}
    expected_source = SUPPORTED_NODES.get(node_id, {}).get("source")
    written = 0
    for item in payload:
        if not isinstance(item, dict):
            continue
        key = str(item.get("credential_key", ""))[:128]
        row = by_key.get(key)
        sample_key = str(item.get("sample_key", ""))[:128]
        if not row or not sample_key:
            continue
        source = str(item.get("source") or expected_source or row.protocol)[:32]
        if expected_source and source != expected_source:
            continue
        try:
            bucket = _utc_naive(datetime.fromisoformat(str(item.get("bucket"))))
        except (TypeError, ValueError):
            bucket = _utc_naive(utcnow())
        statement = (
            insert(FlowRecord)
            .prefix_with("OR IGNORE")
            .values(
                node_id=node_id,
                friend_id=row.friend_id,
                device_id=None,
                bytes_in=_safe_int(item.get("bytes_in")),
                bytes_out=_safe_int(item.get("bytes_out")),
                bucket=bucket,
                source=source,
                sample_key=sample_key,
                created_at=utcnow(),
            )
        )
        result = db.execute(statement)
        written += int(result.rowcount or 0)
    return written


def _mark_applied(db, node_id: int, generation: str, expected: str) -> int:
    if not generation or not hmac.compare_digest(generation, expected):
        return 0
    rows = db.scalars(
        select(UserNodeCredential)
        .join(Friend, Friend.id == UserNodeCredential.friend_id)
        .where(
            UserNodeCredential.node_id == node_id,
            UserNodeCredential.revoked_at.is_(None),
            UserNodeCredential.status.in_(APPLICABLE_CREDENTIAL_STATUSES),
            Friend.enabled.is_(True),
            Friend.per_user_credentials.is_(True),
        )
    ).all()
    changed = 0
    for row in rows:
        if row.status != "grace":
            row.status = "active"
        row.last_synced_at = utcnow()
        row.last_error = ""
        changed += 1
    return changed


def register_agent_routes(app, session_factory, get_db, require_admin):
    @app.get("/api/agent/v1/desired/{node_id}")
    def agent_desired(node_id: int, request: Request, db=Depends(get_db)):
        agent = _require_agent(request, db, node_id)
        node = db.get(Node, node_id)
        if not node or node_id not in REMOTE_AGENT_NODES:
            raise HTTPException(status_code=404, detail="节点 Agent 不存在")
        result = _desired_payload(db, node, agent)
        db.commit()
        return result

    @app.post("/api/agent/v1/heartbeat/{node_id}")
    def agent_heartbeat(
        node_id: int,
        request: Request,
        payload: dict = Body(...),
        db=Depends(get_db),
    ):
        agent = _require_agent(request, db, node_id)
        node = db.get(Node, node_id)
        if not node or node_id not in REMOTE_AGENT_NODES:
            raise HTTPException(status_code=404, detail="节点 Agent 不存在")

        status = str(payload.get("status", "observe"))[:16]
        if status not in {"observe", "ready", "error"}:
            status = "error"
        capabilities = payload.get("capabilities", {})
        if not isinstance(capabilities, dict):
            capabilities = {}
        agent.agent_version = str(payload.get("agent_version", ""))[:64]
        agent.capabilities = json.dumps(
            capabilities, ensure_ascii=True, separators=(",", ":")
        )[:8000]
        agent.last_seen = utcnow()
        agent.updated_at = utcnow()
        agent.applied_generation = str(payload.get("applied_generation", ""))[:128]
        error = str(payload.get("error", ""))[:1000]
        desired = _desired_payload(db, node, agent)
        agent.last_error = error
        agent.status = status
        activated = 0
        if status == "ready" and desired["apply"]:
            activated = _mark_applied(
                db, node_id, agent.applied_generation, desired["generation"]
            )
            if not hmac.compare_digest(agent.applied_generation, desired["generation"]):
                agent.status = "error"
                agent.last_error = "节点 Agent 应用版本未匹配中心期望版本"
        recorded = _record_traffic(db, node_id, payload.get("traffic"))
        db.commit()
        return {
            "ok": True,
            "status": agent.status,
            "desired_generation": desired["generation"],
            "activated_credentials": activated,
            "traffic_records": recorded,
        }

    @app.get("/api/admin/agents")
    def admin_agents(db=Depends(get_db), _=Depends(require_admin)):
        agents = db.scalars(select(NodeAgent).order_by(NodeAgent.node_id)).all()
        return [agent_public_dict(agent) for agent in agents]

    @app.get("/api/admin/agents/{node_id}")
    def admin_agent(node_id: int, db=Depends(get_db), _=Depends(require_admin)):
        agent = db.scalar(select(NodeAgent).where(NodeAgent.node_id == node_id))
        if not agent:
            raise HTTPException(status_code=404, detail="节点 Agent 不存在")
        return agent_public_dict(agent)
