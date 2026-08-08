"""Real latency probes for the admin dashboard.

The probe has three deliberately separate measurements:

* control plane: the US application's local health endpoint;
* node entry: a direct connection to the node's advertised endpoint;
* proxy exit: a request to a public target through the node's real protocol.

Credentials are read only from the local node URI while a probe is running and
are never written to the status file, logs, or API response.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

import yaml
from sqlalchemy import select

from app.converter import parse_uri
from app.models import Node, make_session_factory


ROOT = Path(__file__).resolve().parent.parent
DB_PATH = os.environ.get("SUB_APP_DB", str(ROOT / "data.db"))
STATUS_PATH = Path(
    os.environ.get("SUB_APP_LATENCY_STATUS", "/var/lib/sub-app/latency-status.json")
)
LOCK_PATH = Path(
    os.environ.get("SUB_APP_LATENCY_LOCK", "/var/lib/sub-app/latency-probe.lock")
)
TMP_ROOT = Path(
    os.environ.get("SUB_APP_LATENCY_TMP", "/var/lib/sub-app/latency-tmp")
)
TARGET_URL = os.environ.get(
    "SUB_APP_LATENCY_TARGET", "https://www.gstatic.com/generate_204"
)
LOCAL_HEALTH_URL = os.environ.get(
    "SUB_APP_LATENCY_LOCAL_HEALTH", "http://127.0.0.1:5000/healthz"
)
HYSTERIA_BIN = os.environ.get("SUB_APP_HYSTERIA_BIN", "/usr/local/bin/hysteria")
SING_BOX_BIN = os.environ.get(
    "SUB_APP_SING_BOX_PROBE_BIN", "/usr/local/libexec/sub-app-sing-box-probe"
)

TIME_RE = re.compile(r'"time"\s*:\s*"([0-9]+(?:\.[0-9]+)?)ms"')


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json(payload: dict) -> None:
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(
        prefix="latency-status.", suffix=".json", dir=str(STATUS_PATH.parent)
    )
    path = Path(name)
    try:
        os.chmod(path, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=True, separators=(",", ":"))
            handle.write("\n")
        os.replace(path, STATUS_PATH)
    finally:
        path.unlink(missing_ok=True)


def read_status() -> dict:
    try:
        value = json.loads(STATUS_PATH.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {"status": "never_run"}
    except (FileNotFoundError, OSError, ValueError):
        return {
            "status": "never_run",
            "started_at": None,
            "finished_at": None,
            "control": {"state": "pending", "value": "未测试"},
            "summary": {"nodes_total": 0, "entry_ok": 0, "proxy_ok": 0},
            "nodes": [],
            "target": {"url": TARGET_URL},
        }


def start_probe() -> dict:
    """Start one detached probe run; the API remains responsive."""

    if LOCK_PATH.exists():
        return {"ok": True, "started": False, "status": "running"}
    script = ROOT / "scripts" / "latency_probe.py"
    try:
        subprocess.Popen(
            [os.environ.get("SUB_APP_PYTHON", str(ROOT / "venv/bin/python")), str(script)],
            cwd=str(ROOT),
            env=os.environ.copy(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except (OSError, subprocess.SubprocessError):
        return {"ok": False, "started": False, "status": "error"}
    return {"ok": True, "started": True, "status": "running"}


def _result(state: str, ms: int | None = None, reason: str = "", source: str = "") -> dict:
    value = "正常" if state == "ok" else (
        "未就绪" if state == "unsupported" else "探测中" if state == "pending" else "不可达"
    )
    if state == "ok" and ms is not None:
        value = f"{ms} ms"
    payload = {"state": state, "value": value}
    if ms is not None:
        payload["ms"] = int(ms)
    if reason:
        payload["reason"] = reason
    if source:
        payload["source"] = source
    return payload


def _remark_node(node: Node, parsed: dict | None) -> bool:
    if str(node.name or "").startswith("⚠️"):
        return True
    if not parsed:
        return False
    return parsed.get("host") == "127.0.0.1" or int(parsed.get("port") or 0) <= 3


def _endpoint(host: str, port: int) -> str:
    return f"[{host}]:{port}" if ":" in host and not host.startswith("[") else f"{host}:{port}"


def _socket_probe(host: str, port: int, timeout: float = 4.0) -> dict:
    started = time.perf_counter()
    last_error = "连接失败"
    try:
        addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return _result("bad", reason="DNS 解析失败", source="tcp")
    for family, sock_type, proto, _canonname, sockaddr in addresses:
        sock = socket.socket(family, sock_type, proto)
        try:
            sock.settimeout(timeout)
            sock.connect(sockaddr)
            elapsed = max(1, round((time.perf_counter() - started) * 1000))
            return _result("ok", elapsed, source="tcp")
        except TimeoutError:
            last_error = "连接超时"
        except OSError as exc:
            last_error = "连接被拒绝" if getattr(exc, "errno", None) in {111, 61, 10061} else "连接失败"
        finally:
            sock.close()
    return _result("bad", reason=last_error, source="tcp")


def _local_probe() -> dict:
    started = time.perf_counter()
    try:
        request = Request(LOCAL_HEALTH_URL, headers={"Accept": "application/json"})
        with urlopen(request, timeout=4) as response:
            response.read(1024)
        return _result("ok", max(1, round((time.perf_counter() - started) * 1000)), source="healthz")
    except Exception:
        return _result("bad", reason="控制面不可达", source="healthz")


def _target_address() -> str:
    parsed = urlsplit(TARGET_URL)
    host = parsed.hostname or "www.gstatic.com"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return _endpoint(host, port)


def _hysteria_probe(parsed: dict) -> dict:
    auth = parsed.get("user") or parsed.get("password")
    if not auth or not shutil.which(HYSTERIA_BIN):
        return _result("unsupported", reason="HY2 探测器未就绪", source="hysteria")
    TMP_ROOT.mkdir(parents=True, exist_ok=True)
    os.chmod(TMP_ROOT, 0o700)
    config = {
        "server": _endpoint(parsed["host"], int(parsed["port"])),
        "auth": auth,
        "tls": {
            "sni": parsed.get("params", {}).get("sni") or parsed["host"],
            "insecure": str(parsed.get("params", {}).get("insecure", "")).lower()
            in {"1", "true"},
        },
    }
    params = parsed.get("params", {})
    if params.get("obfs"):
        config["obfs"] = {
            "type": params["obfs"],
            "salamander": {"password": params.get("obfs-password", "")},
        }
    path = None
    try:
        fd, name = tempfile.mkstemp(prefix="hy2-", suffix=".yaml", dir=str(TMP_ROOT))
        path = Path(name)
        os.chmod(path, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            yaml.safe_dump(config, handle, allow_unicode=True, sort_keys=False)
        command = [
            HYSTERIA_BIN,
            "--disable-update-check",
            "--log-level",
            "info",
            "-c",
            str(path),
            "ping",
            _target_address(),
        ]
        completed = subprocess.run(
            command, capture_output=True, text=True, timeout=14, check=False
        )
        output = (completed.stdout or "") + "\n" + (completed.stderr or "")
        match = TIME_RE.search(output)
        if completed.returncode == 0 and match:
            return _result("ok", max(1, round(float(match.group(1)))), source="hysteria-ping")
        if completed.returncode == 0:
            return _result("ok", source="hysteria-ping")
        return _result("bad", reason="HY2 握手或出口失败", source="hysteria-ping")
    except subprocess.TimeoutExpired:
        return _result("bad", reason="代理出口超时", source="hysteria-ping")
    except (OSError, ValueError, yaml.YAMLError):
        return _result("bad", reason="HY2 探测失败", source="hysteria-ping")
    finally:
        if path is not None:
            path.unlink(missing_ok=True)


def _sing_box_config(parsed: dict, port: int) -> dict:
    params = parsed.get("params", {})
    security = params.get("security", "")
    tls = {"enabled": security in {"tls", "reality"}}
    if params.get("sni"):
        tls["server_name"] = params["sni"]
    if params.get("fp"):
        tls["utls"] = {"enabled": True, "fingerprint": params["fp"]}
    if security == "reality":
        tls["reality"] = {
            "enabled": True,
            "public_key": params.get("pbk", ""),
            "short_id": params.get("sid", ""),
        }
    outbound = {
        "type": "vless",
        "tag": "proxy",
        "server": parsed["host"],
        "server_port": int(parsed["port"]),
        "uuid": parsed.get("user", ""),
        "tls": tls,
    }
    if params.get("flow"):
        outbound["flow"] = params["flow"]
    if params.get("type") == "ws":
        transport = {"type": "ws", "path": params.get("path", "/")}
        if params.get("host"):
            transport["headers"] = {"Host": params["host"]}
        outbound["transport"] = transport
    elif params.get("type") == "grpc":
        outbound["transport"] = {
            "type": "grpc", "service_name": params.get("serviceName", "")
        }
    return {
        "log": {"disabled": True},
        "inbounds": [{
            "type": "mixed", "tag": "mixed-in", "listen": "127.0.0.1", "listen_port": port
        }],
        "outbounds": [outbound, {"type": "direct", "tag": "direct"}],
        "route": {"final": "proxy"},
    }


def _sing_box_probe(parsed: dict) -> dict:
    if not parsed.get("user") or not Path(SING_BOX_BIN).is_file():
        return _result("unsupported", reason="VLESS 探测器未就绪", source="sing-box")
    port_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    port_sock.bind(("127.0.0.1", 0))
    port = port_sock.getsockname()[1]
    port_sock.close()
    TMP_ROOT.mkdir(parents=True, exist_ok=True)
    os.chmod(TMP_ROOT, 0o700)
    path = None
    process = None
    try:
        fd, name = tempfile.mkstemp(prefix="vless-", suffix=".json", dir=str(TMP_ROOT))
        path = Path(name)
        os.chmod(path, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(_sing_box_config(parsed, port), handle, ensure_ascii=False)
        checked = subprocess.run(
            [SING_BOX_BIN, "check", "-c", str(path)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=20, check=False,
        )
        if checked.returncode:
            return _result("bad", reason="VLESS 配置校验失败", source="sing-box")
        process = subprocess.Popen(
            [SING_BOX_BIN, "run", "-c", str(path)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        ready = False
        for _ in range(40):
            if process.poll() is not None:
                break
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    ready = True
                    break
            except OSError:
                time.sleep(0.1)
        if not ready:
            return _result("bad", reason="VLESS 探测器未启动", source="sing-box")
        curl = shutil.which("curl") or "/usr/bin/curl"
        started = time.perf_counter()
        completed = subprocess.run(
            [curl, "--proxy", f"socks5h://127.0.0.1:{port}", "-sS", "-o", os.devnull,
             "-w", "%{http_code}", "--connect-timeout", "5", "--max-time", "12", TARGET_URL],
            capture_output=True, text=True, timeout=18, check=False,
        )
        code = (completed.stdout or "").strip()
        if completed.returncode == 0 and code[:1] in {"2", "3"}:
            return _result("ok", max(1, round((time.perf_counter() - started) * 1000)), source="sing-box")
        return _result("bad", reason="VLESS 代理出口失败", source="sing-box")
    except subprocess.TimeoutExpired:
        return _result("bad", reason="代理出口超时", source="sing-box")
    except (OSError, ValueError, TypeError):
        return _result("bad", reason="VLESS 探测失败", source="sing-box")
    finally:
        if process is not None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        if path is not None:
            path.unlink(missing_ok=True)


def _probe_node(node: dict) -> dict:
    parsed = parse_uri(node["uri"])
    if not parsed or not parsed.get("host") or not parsed.get("port"):
        return {
            "node_id": node["id"], "name": node["name"], "protocol": node["protocol"],
            "entry": _result("bad", reason="节点链接无效"),
            "proxy": _result("bad", reason="节点链接无效"),
        }
    protocol = parsed.get("scheme", node["protocol"])
    if protocol == "hysteria2":
        proxy = _hysteria_probe(parsed)
        entry = dict(proxy)
        entry["source"] = "hysteria-handshake"
        entry["reason"] = "HY2 握手与代理出口同次验证" if proxy.get("state") == "ok" else proxy.get("reason", "")
    elif protocol == "vless":
        entry = _socket_probe(parsed["host"], int(parsed["port"]))
        proxy = _sing_box_probe(parsed)
    else:
        entry = _socket_probe(parsed["host"], int(parsed["port"]))
        proxy = _result("unsupported", reason="协议暂未适配", source="probe")
    return {
        "node_id": node["id"], "name": node["name"], "protocol": protocol,
        "entry": entry, "proxy": proxy,
    }


def _summary(nodes: list[dict]) -> dict:
    def count(kind: str) -> int:
        return sum(row.get(kind, {}).get("state") == "ok" for row in nodes)

    def average(kind: str) -> int | None:
        values = [row[kind].get("ms") for row in nodes if row.get(kind, {}).get("ms") is not None]
        return round(sum(values) / len(values)) if values else None

    return {
        "nodes_total": len(nodes),
        "entry_ok": count("entry"),
        "proxy_ok": count("proxy"),
        "entry_avg_ms": average("entry"),
        "proxy_avg_ms": average("proxy"),
    }


def run_probes() -> None:
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(LOCK_PATH), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
    except FileExistsError:
        return
    started = _now()
    try:
        _write_json({
            "status": "running", "started_at": started, "finished_at": None,
            "control": {"state": "pending", "value": "探测中"},
            "summary": {"nodes_total": 0, "entry_ok": 0, "proxy_ok": 0},
            "nodes": [], "target": {"url": TARGET_URL},
        })
        factory = make_session_factory(DB_PATH)
        db = factory()
        try:
            candidates = db.scalars(select(Node).where(Node.enabled.is_(True)).order_by(Node.sort_order, Node.id)).all()
            nodes = []
            for item in candidates:
                parsed = parse_uri(item.uri)
                if _remark_node(item, parsed):
                    continue
                nodes.append({"id": item.id, "name": item.name, "protocol": item.protocol, "uri": item.uri})
        finally:
            db.close()
        with ThreadPoolExecutor(max_workers=min(6, max(1, len(nodes)))) as pool:
            results = list(pool.map(_probe_node, nodes))
        summary = _summary(results)
        all_ok = bool(results) and summary["proxy_ok"] == summary["nodes_total"]
        _write_json({
            "status": "success" if all_ok else "partial",
            "started_at": started, "finished_at": _now(),
            "control": _local_probe(), "summary": summary, "nodes": results,
            "target": {"url": TARGET_URL},
        })
    except Exception as exc:
        _write_json({
            "status": "error", "started_at": started, "finished_at": _now(),
            "control": {"state": "bad", "value": "不可用", "reason": "探测任务失败"},
            "summary": {"nodes_total": 0, "entry_ok": 0, "proxy_ok": 0},
            "nodes": [], "target": {"url": TARGET_URL}, "error": type(exc).__name__,
        })
    finally:
        try:
            LOCK_PATH.unlink()
        except FileNotFoundError:
            pass
