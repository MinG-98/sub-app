"""Drive Agent.apply_vless end to end, not a copy of its logic.

test_vless_flow_sources in test_flow_survives_window.py unit-tests
Agent._vless_flow directly, which is good but not sufficient on its own: a
mutation test proved that reverting apply_vless's call site back to the
pre-fix inline expression (deriving flow from the windowed `users` list
instead of calling _vless_flow) left that file's tests fully green, because
neither of them calls apply_vless. That is precisely the bug that took five
VLESS nodes down 24h after their agent's first apply while reporting
healthy the whole time.

This module patches out the only things apply_vless cannot do without root
or a real sing-box binary (the version/check subprocess calls and the
service restart) and otherwise runs the real method against a real temp
config file and a real state file, including across a simulated process
restart.
"""

import importlib.util
import json
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "agent", Path(__file__).resolve().parent.parent / "node-agent.py"
)
agent_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent_mod)


class _FakeCheck:
    returncode = 0


def _make_agent(tmp_path, state=None):
    """A real Agent instance with I/O redirected into tmp_path, skipping
    Agent.__init__ (which reads a real state file and starts nothing)."""
    agent = agent_mod.Agent.__new__(agent_mod.Agent)
    agent.state_path = tmp_path / "state.json"
    agent.state = state if state is not None else {"nodes": {}}
    agent.config_meta = {}
    return agent


def _seed_config(tmp_path, initial_users):
    config = {
        "inbounds": [
            {
                "tag": "vless-reality",
                "type": "vless",
                "users": initial_users,
            }
        ],
        "experimental": {},
    }
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    return path


def _spec(tmp_path, config_path, node_id=17, **extra):
    return {
        "node_id": node_id,
        "sing_box_binary": "sing-box",
        "sing_box_config": str(config_path),
        "backup_dir": str(tmp_path / "backups"),
        "service": "sing-box.service",
        "v2ray_api_listen": "127.0.0.1:10085",
        **extra,
    }


def _patch_subprocess(monkeypatch, restart_calls):
    """Stub the version check, the config check, and the service restart —
    the three things apply_vless cannot do in a test sandbox — and count
    restarts so the idempotency guard can be checked for real."""
    monkeypatch.setattr(
        agent_mod, "command_output", lambda argv, timeout=8: "with_v2ray_api"
    )
    monkeypatch.setattr(agent_mod.subprocess, "run", lambda *a, **k: _FakeCheck())

    def fake_restart(self, spec):
        restart_calls.append(1)
        return True

    monkeypatch.setattr(agent_mod.Agent, "_service_restart", fake_restart)


def _users_of(config_path):
    config = json.loads(config_path.read_text(encoding="utf-8"))
    return config["inbounds"][0]["users"]


def test_apply_vless_survives_the_window_through_the_real_method(tmp_path, monkeypatch):
    restart_calls = []
    _patch_subprocess(monkeypatch, restart_calls)

    config_path = _seed_config(
        tmp_path,
        initial_users=[
            {"uuid": "legacy-uuid", "flow": "xtls-rprx-vision"},
        ],
    )
    agent = _make_agent(tmp_path)
    spec = _spec(tmp_path, config_path)

    # First apply: captures the legacy snapshot and restarts once.
    agent.apply_vless(spec, {"users": [{"stats_id": "f9n17v1", "uuid": "u1"}]})
    assert len(restart_calls) == 1
    users = _users_of(config_path)
    assert {u["name"]: u.get("flow") for u in users if u.get("name")} == {
        "f9n17v1": "xtls-rprx-vision"
    }

    # Second apply, same desired state: idempotency guard must hold — the
    # rendered config is byte-identical, so no backup/check/restart cycle.
    agent.apply_vless(spec, {"users": [{"stats_id": "f9n17v1", "uuid": "u1"}]})
    assert len(restart_calls) == 1, "unchanged config must not restart the proxy"

    # Force the legacy window shut without touching legacy_users, exactly
    # what happens 24h after the first apply in production.
    agent.state["nodes"][str(spec["node_id"])]["legacy_until"] = (
        agent_mod.utcnow().replace(year=2000).isoformat()
    )
    agent.apply_vless(
        spec,
        {
            "users": [
                {"stats_id": "f9n17v1", "uuid": "u1"},
                {"stats_id": "f9n17v2", "uuid": "u2"},
            ]
        },
    )
    users = _users_of(config_path)
    named = {u["name"]: u.get("flow") for u in users if u.get("name")}
    assert named == {
        "f9n17v1": "xtls-rprx-vision",
        "f9n17v2": "xtls-rprx-vision",
    }, "flow must survive the window closing when driven through apply_vless itself"
    assert len(restart_calls) == 2, "the flow-bearing config change must restart once"

    # Simulate a process restart: a fresh Agent reads the persisted state
    # back from disk rather than carrying it in memory.
    reloaded_state = agent_mod.read_json(agent.state_path, {"nodes": {}})
    fresh_agent = _make_agent(tmp_path, state=reloaded_state)
    fresh_agent.apply_vless(
        spec,
        {
            "users": [
                {"stats_id": "f9n17v1", "uuid": "u1"},
                {"stats_id": "f9n17v2", "uuid": "u2"},
                {"stats_id": "f9n17v3", "uuid": "u3"},
            ]
        },
    )
    users = _users_of(config_path)
    named = {u["name"]: u.get("flow") for u in users if u.get("name")}
    assert named == {
        "f9n17v1": "xtls-rprx-vision",
        "f9n17v2": "xtls-rprx-vision",
        "f9n17v3": "xtls-rprx-vision",
    }, "flow must survive an agent process restart, not just stay in memory"


def test_apply_vless_reverted_flow_lookup_fails_this_test(tmp_path, monkeypatch):
    """Pin the call site itself. Reverting `flow = self._vless_flow(state,
    spec)` back to the pre-fix inline expression must fail here even though
    it would still pass test_vless_flow_sources, which only calls
    _vless_flow directly and never apply_vless."""
    restart_calls = []
    _patch_subprocess(monkeypatch, restart_calls)

    config_path = _seed_config(
        tmp_path,
        initial_users=[{"uuid": "legacy-uuid", "flow": "xtls-rprx-vision"}],
    )
    agent = _make_agent(tmp_path)
    spec = _spec(tmp_path, config_path)

    agent.apply_vless(spec, {"users": [{"stats_id": "f9n17v1", "uuid": "u1"}]})
    agent.state["nodes"][str(spec["node_id"])]["legacy_until"] = (
        agent_mod.utcnow().replace(year=2000).isoformat()
    )
    agent.apply_vless(spec, {"users": [{"stats_id": "f9n17v1", "uuid": "u1"}]})

    users = _users_of(config_path)
    assert all(u.get("flow") for u in users if u.get("name"))


def test_apply_vless_brand_new_node_has_no_flow_without_the_fallback_key(
    tmp_path, monkeypatch
):
    """Documents a real gap: a node whose inbound has no pre-existing named
    users (a brand-new deploy, not a migration) captures an empty legacy
    snapshot, so flow is dropped from the very first apply — no 24h grace,
    and nothing warns the operator. The only way out today is setting
    vless_flow in that node's agent config."""
    restart_calls = []
    _patch_subprocess(monkeypatch, restart_calls)

    config_path = _seed_config(tmp_path, initial_users=[])
    agent = _make_agent(tmp_path)
    spec = _spec(tmp_path, config_path)

    agent.apply_vless(spec, {"users": [{"stats_id": "f9n17v1", "uuid": "u1"}]})
    users = _users_of(config_path)
    assert all("flow" not in u for u in users), (
        "known gap: brand-new nodes get no flow until vless_flow is set "
        "in their agent config — see agent/README.md"
    )

    # The fallback key closes the gap.
    agent2 = _make_agent(tmp_path)
    config_path2 = _seed_config(tmp_path, initial_users=[])
    spec2 = _spec(tmp_path, config_path2, node_id=18, vless_flow="xtls-rprx-vision")
    agent2.apply_vless(spec2, {"users": [{"stats_id": "f9n18v1", "uuid": "u1"}]})
    users2 = _users_of(config_path2)
    assert all(u.get("flow") == "xtls-rprx-vision" for u in users2)
