"""Check that the shared stats rebuild converges for a host running both a
VLESS and a Hysteria2 node inside one sing-box."""

import importlib.util
import json
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "agent", Path(__file__).resolve().parent.parent / "node-agent.py"
)
agent_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent_mod)


def test_stats_union_converges():
    agent = agent_mod.Agent.__new__(agent_mod.Agent)

    config = {
        "inbounds": [
            {
                "tag": "vless-reality",
                "type": "vless",
                "users": [
                    {"uuid": "legacy", "flow": "xtls-rprx-vision"},
                    {"name": "f3n5v1", "uuid": "u1", "flow": "xtls-rprx-vision"},
                    {"name": "f4n5v1", "uuid": "u2", "flow": "xtls-rprx-vision"},
                ],
            },
            {"tag": "hy2", "type": "hysteria2", "users": [{"password": "legacy"}]},
        ],
        "experimental": {},
    }
    node_spec = {"v2ray_api_listen": "127.0.0.1:10085"}

    # First the VLESS node applies; the Hysteria2 inbound has no named users yet.
    agent._singbox_stats_section(config, node_spec)
    after_vless = json.loads(json.dumps(config["experimental"]["v2ray_api"]))
    assert after_vless["stats"]["users"] == ["f3n5v1", "f4n5v1"]
    assert after_vless["stats"]["inbounds"] == ["vless-reality"]

    # Then the Hysteria2 node applies and adds its own named users.
    config["inbounds"][1]["users"] = [
        {"password": "legacy"},
        {"name": "f3n6v1", "password": "f3n6v1:secret"},
        {"name": "f4n6v1", "password": "f4n6v1:secret"},
    ]
    agent._singbox_stats_section(config, node_spec)
    after_hy2 = json.loads(json.dumps(config["experimental"]["v2ray_api"]))
    assert after_hy2["stats"]["users"] == [
        "f3n5v1",
        "f4n5v1",
        "f3n6v1",
        "f4n6v1",
    ], "union lost users"
    assert after_hy2["stats"]["inbounds"] == ["vless-reality", "hy2"]

    # A second VLESS apply must not drop the Hysteria2 users, or the two nodes
    # would rewrite and restart the core on every poll.
    agent._singbox_stats_section(config, node_spec)
    after_vless2 = json.loads(json.dumps(config["experimental"]["v2ray_api"]))
    assert after_vless2 == after_hy2, "not idempotent: applies would flip-flop"
