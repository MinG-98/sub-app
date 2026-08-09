#!/usr/bin/env python3
"""Reconcile user allocations with the local US proxy adapters.

The admin UI/API is the desired-state writer.  This bounded agent is the
eventual-consistency and retry loop, so adding a user or checking a node does
not require a manual server operation.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.credentials import ensure_credential  # noqa: E402
from app.models import (  # noqa: E402
    Allocation,
    Friend,
    Node,
    UserNodeCredential,
    make_session_factory,
    utcnow,
)
from app.proxy_adapters import (  # noqa: E402
    SUPPORTED_NODES,
    activate_credential,
    node_per_user_enabled,
    sync_vless_config,
)

STATUS_PATH = Path(
    os.environ.get(
        "SUB_APP_RECONCILER_STATUS", "/var/lib/sub-app/reconciler-status.json"
    )
)


def reconcile(db):
    activated = 0
    errors = 0
    revoked = 0
    vless_changed = False
    friends = db.scalars(select(Friend).order_by(Friend.id)).all()
    for friend in friends:
        allocations = db.scalars(
            select(Allocation).where(Allocation.friend_id == friend.id)
        ).all()
        wanted = {item.node_id for item in allocations}
        rows = db.scalars(
            select(UserNodeCredential).where(UserNodeCredential.friend_id == friend.id)
        ).all()
        if not friend.enabled or not friend.per_user_credentials:
            for row in rows:
                if row.revoked_at is None:
                    row.status = "revoked"
                    row.revoked_at = utcnow()
                    row.grace_until = None
                    revoked += 1
                    vless_changed = vless_changed or row.node_id == 11
            continue

        for node_id in wanted:
            info = SUPPORTED_NODES.get(node_id)
            node = db.get(Node, node_id)
            if (
                not info
                or not node
                or not node_per_user_enabled(node)
                or node.protocol != info["protocol"]
            ):
                continue
            row = ensure_credential(db, friend, node)
            if row.status not in {"active", "grace"}:
                activate_credential(db, row)
                if row.status == "active":
                    activated += 1
                else:
                    errors += 1
                vless_changed = vless_changed or node_id == 11

        for row in rows:
            if row.node_id not in wanted and row.revoked_at is None:
                row.status = "revoked"
                row.revoked_at = utcnow()
                row.grace_until = None
                revoked += 1
                vless_changed = vless_changed or row.node_id == 11

    if vless_changed:
        try:
            sync_vless_config(db)
        except Exception:
            # Individual rows already carry their adapter error; keep the
            # transaction usable and let the next timer retry.
            pass
    db.commit()
    return {"activated": activated, "errors": errors, "revoked": revoked}


def write_status(payload):
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(
        prefix="reconciler.", suffix=".json", dir=str(STATUS_PATH.parent)
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
    db_path = os.environ.get("SUB_APP_DB", str(ROOT / "data.db"))
    factory = make_session_factory(db_path)
    db = factory()
    started = datetime.now(timezone.utc)
    try:
        result = reconcile(db)
        status = "success" if result["errors"] == 0 else "partial"
        payload = {"status": status, "at": started.isoformat(), **result}
        write_status(payload)
        print(json.dumps({"ok": True, **payload}, ensure_ascii=True))
    except Exception as exc:
        db.rollback()
        payload = {
            "status": "error",
            "at": started.isoformat(),
            "error": type(exc).__name__,
        }
        write_status(payload)
        print(json.dumps({"ok": False, **payload}, ensure_ascii=True))
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
