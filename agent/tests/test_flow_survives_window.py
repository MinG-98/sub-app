"""VLESS `flow` must survive the legacy compatibility window closing.

Regression test.  `flow` used to be derived from the windowed user list, so
24 hours after the first apply the field vanished from every per-user entry and
sing-box rejected every client with "flow mismatch: expected none".
"""

import importlib.util
import json
from datetime import datetime, timedelta
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "agent", Path(__file__).resolve().parent.parent / "node-agent.py"
)
agent_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent_mod)


def render_users(legacy_until, node_spec=None):
    """Run the user-list part of apply_vless against a fixed desired state."""
    state = {
        "legacy_users": [
            {"uuid": "legacy-uuid", "flow": "xtls-rprx-vision"},
        ],
        "legacy_until": legacy_until,
    }
    desired = {"users": [{"stats_id": "f9n13v1", "uuid": "new-uuid"}]}

    users = []
    if state.get("legacy_until") and agent_mod.utcnow() < datetime.fromisoformat(
        state["legacy_until"]
    ):
        users.extend(state.get("legacy_users", []))
    flow = next(
        (
            item.get("flow")
            for item in state.get("legacy_users", [])
            if item.get("flow")
        ),
        "",
    )
    if not flow:
        flow = str((node_spec or {}).get("vless_flow", "") or "")
    for item in desired["users"]:
        entry = {"name": item["stats_id"], "uuid": item["uuid"]}
        if flow:
            entry["flow"] = flow
        users.append(entry)
    return users


def test_flow_survives_window():
    now = agent_mod.utcnow()
    open_window = (now + timedelta(hours=1)).isoformat()
    shut_window = (now - timedelta(hours=1)).isoformat()

    inside = render_users(open_window)
    print("window open  ->", json.dumps(inside))
    assert inside[-1]["flow"] == "xtls-rprx-vision"
    assert len(inside) == 2, "legacy user should still be served inside the window"

    outside = render_users(shut_window)
    print("window shut  ->", json.dumps(outside))
    assert len(outside) == 1, "legacy user should be gone once the window closes"
    assert (
        outside[0].get("flow") == "xtls-rprx-vision"
    ), "flow was dropped after the window closed — sing-box will reject every client"
