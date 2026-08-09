"""Regression tests for public health and agent traffic boundaries."""

# Environment variables must be set before importing app.main.
# ruff: noqa: E402

from __future__ import annotations

import os
import tempfile
from pathlib import Path

_TEST_ROOT = Path(tempfile.mkdtemp(prefix="sub-app-tests-"))
os.environ.setdefault("SUB_APP_ADMIN_PASSWORD", "test-admin-password")
os.environ.setdefault("SUB_APP_SECRET", "test-secret-for-tests")
os.environ["SUB_APP_DB"] = str(_TEST_ROOT / "app.db")
os.environ["SUB_APP_PROXY_STATUS"] = str(_TEST_ROOT / "proxy-status.json")
os.environ["SUB_APP_RECONCILER_STATUS"] = str(_TEST_ROOT / "reconciler-status.json")

from fastapi.testclient import TestClient

from app.agent_api import _mark_applied, _record_traffic
from app.credentials import credential_stats_id
from app.main import Session, app
from app.models import (
    Allocation,
    FlowRecord,
    Friend,
    Node,
    UserNodeCredential,
    make_session_factory,
)

client = TestClient(app, base_url="https://testserver")


def _seed_subscription() -> None:
    db = Session()
    try:
        if db.query(Friend).filter_by(uid="security-test-user").first():
            return
        friend = Friend(
            uid="security-test-user",
            token="security-test-token",
            remark="test",
        )
        node = Node(
            name="Security Test",
            protocol="vless",
            uri=(
                "vless://00000000-0000-0000-0000-000000000001"
                "@example.com:443?security=tls#Security-Test"
            ),
            server="example.com",
        )
        db.add_all([friend, node])
        db.flush()
        db.add(Allocation(friend_id=friend.id, node_id=node.id))
        db.commit()
    finally:
        db.close()


def test_public_health_is_liveness_only() -> None:
    response = client.get("/healthz")

    assert response.status_code == 200
    assert set(response.json()) == {"ok", "generated_at"}
    assert client.get("/api/admin/healthz").status_code == 401


def test_detailed_health_requires_admin_authentication() -> None:
    login = client.post("/api/admin/login", json={"password": "test-admin-password"})
    assert login.status_code == 200

    response = client.get("/api/admin/healthz")

    assert response.status_code == 200
    assert {"collector", "agents", "adapters"} <= set(response.json())


def test_subscription_is_explicitly_not_cacheable() -> None:
    _seed_subscription()

    response = client.get("/sub/security-test-token?target=raw")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["pragma"] == "no-cache"


def _agent_db(tmp_path: Path):
    return make_session_factory(str(tmp_path / "agent.db"))


def _add_agent_fixture(
    db, *, friend: Friend, node: Node, status: str, version: int = 1
):
    row = UserNodeCredential(
        friend_id=friend.id,
        node_id=node.id,
        protocol=node.protocol,
        credential_name=f"u{friend.id}n{node.id}v{version}",
        version=version,
        status=status,
    )
    db.add(row)
    db.flush()
    return row


def test_agent_traffic_accepts_only_active_enabled_user_credentials(tmp_path: Path):
    session_factory = _agent_db(tmp_path)
    db = session_factory()
    try:
        node = Node(
            id=5,
            name="Remote VLESS",
            protocol="vless",
            uri="vless://uuid@example.com:443#remote",
        )
        active_friend = Friend(uid="active", token="active-token")
        disabled_friend = Friend(uid="disabled", token="disabled-token", enabled=False)
        pending_friend = Friend(uid="pending", token="pending-token")
        for friend in (active_friend, disabled_friend, pending_friend):
            friend.per_user_credentials = True
        db.add_all([node, active_friend, disabled_friend, pending_friend])
        db.flush()
        active = _add_agent_fixture(
            db, friend=active_friend, node=node, status="active"
        )
        _add_agent_fixture(db, friend=disabled_friend, node=node, status="active")
        _add_agent_fixture(db, friend=pending_friend, node=node, status="pending")

        payload = [
            {
                "credential_key": credential_stats_id(active),
                "sample_key": "accepted",
                "source": "vless",
                "bucket": "2026-08-09T00:00:00+00:00",
                "bytes_in": 10,
                "bytes_out": 20,
            },
            {
                "credential_key": credential_stats_id(active),
                "sample_key": "wrong-source",
                "source": "hysteria2",
                "bucket": "2026-08-09T00:00:00+00:00",
                "bytes_in": 100,
                "bytes_out": 100,
            },
        ]

        assert _record_traffic(db, node.id, payload) == 1
        record = db.query(FlowRecord).one()
        assert (record.bytes_in, record.bytes_out, record.source) == (10, 20, "vless")
    finally:
        db.close()


def test_agent_apply_does_not_reactivate_disabled_or_grace_credentials(tmp_path: Path):
    session_factory = _agent_db(tmp_path)
    db = session_factory()
    try:
        node = Node(name="Remote Hy2", protocol="hysteria2", uri="hysteria2://x")
        enabled = Friend(uid="enabled", token="enabled-token")
        enabled.per_user_credentials = True
        disabled = Friend(uid="disabled", token="disabled-token", enabled=False)
        disabled.per_user_credentials = True
        db.add_all([node, enabled, disabled])
        db.flush()
        active = _add_agent_fixture(db, friend=enabled, node=node, status="active")
        pending = _add_agent_fixture(
            db, friend=enabled, node=node, status="pending", version=2
        )
        grace = _add_agent_fixture(
            db, friend=enabled, node=node, status="grace", version=3
        )
        disabled_row = _add_agent_fixture(
            db, friend=disabled, node=node, status="pending"
        )

        assert _mark_applied(db, node.id, "generation", "generation") == 2
        assert active.status == "active"
        assert pending.status == "active"
        assert grace.status == "grace"
        assert disabled_row.status == "pending"
    finally:
        db.close()
