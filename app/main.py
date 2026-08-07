import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import Body, Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from itsdangerous import BadSignature, URLSafeTimedSerializer
from sqlalchemy import func, select

from app.converter import parse_uri, render
from app.models import (
    Allocation,
    Device,
    FetchLog,
    Friend,
    Node,
    make_session_factory,
    new_token,
    utcnow,
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


def node_dict(n: Node, alloc_count: int = 0):
    parsed = parse_uri(n.uri) or {}
    return {
        "id": n.id,
        "name": n.name,
        "protocol": n.protocol,
        "server": n.server,
        "port": parsed.get("port", 0),
        "enabled": n.enabled,
        "sort_order": n.sort_order,
        "nezha_server_id": n.nezha_server_id,
        "allocated_to": alloc_count,
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
    return [node_dict(n, counts.get(n.id, 0)) for n in nodes]


@app.post("/api/admin/nodes")
def create_nodes(
    payload: dict = Body(...), db=Depends(get_db), _=Depends(require_admin)
):
    """Accepts either a single node or a bulk paste of URIs."""
    bulk = payload.get("bulk", "")
    created, skipped = [], []

    raw_list = [l.strip() for l in bulk.splitlines() if l.strip()] if bulk else []
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
            name=payload.get("name") or parsed["name"] or f"{parsed['scheme']}-{parsed['host']}",
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
    node_id: int, payload: dict = Body(...), db=Depends(get_db), _=Depends(require_admin)
):
    node = db.get(Node, node_id)
    if not node:
        raise HTTPException(404, "节点不存在")
    for field in ("name", "enabled", "sort_order", "nezha_server_id"):
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
    return node_dict(node)


@app.delete("/api/admin/nodes/{node_id}")
def delete_node(node_id: int, db=Depends(get_db), _=Depends(require_admin)):
    node = db.get(Node, node_id)
    if not node:
        raise HTTPException(404, "节点不存在")
    db.delete(node)
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- friends


def friend_dict(f: Friend, node_ids: list[int], device_count: int = 0):
    base = PUBLIC_BASE or ""
    return {
        "id": f.id,
        "uid": f.uid,
        "remark": f.remark,
        "token": f.token,
        "enabled": f.enabled,
        "flow_limit_gb": f.flow_limit_gb,
        "device_limit": f.device_limit,
        "node_ids": node_ids,
        "device_count": device_count,
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
        friend_dict(f, by_friend.get(f.id, []), dev_counts.get(f.id, 0)) for f in friends
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
        token=new_token(),
    )
    db.add(friend)
    db.flush()
    for nid in payload.get("node_ids", []):
        db.add(Allocation(friend_id=friend.id, node_id=int(nid)))
    db.commit()
    return friend_dict(friend, [int(n) for n in payload.get("node_ids", [])])


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

    for field in ("remark", "enabled", "flow_limit_gb", "device_limit"):
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

    db.commit()
    node_ids = [
        a.node_id
        for a in db.scalars(
            select(Allocation).where(Allocation.friend_id == friend_id)
        ).all()
    ]
    return friend_dict(friend, node_ids)


@app.delete("/api/admin/friends/{friend_id}")
def delete_friend(friend_id: int, db=Depends(get_db), _=Depends(require_admin)):
    friend = db.get(Friend, friend_id)
    if not friend:
        raise HTTPException(404, "用户不存在")
    db.delete(friend)
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- devices


@app.get("/api/admin/devices")
def list_devices(db=Depends(get_db), _=Depends(require_admin)):
    rows = db.execute(
        select(Device, Friend.uid).join(Friend, Device.friend_id == Friend.id)
    ).all()
    out = []
    for dev, uid in rows:
        out.append(
            {
                "id": dev.id,
                "friend_id": dev.friend_id,
                "friend_uid": uid,
                "fingerprint": dev.fingerprint,
                "label": dev.label,
                "user_agent": dev.user_agent,
                "last_ip": dev.last_ip,
                "fetch_count": dev.fetch_count,
                "blocked": dev.blocked,
                "first_seen": dev.first_seen.isoformat(),
                "last_seen": dev.last_seen.isoformat(),
            }
        )
    out.sort(key=lambda d: d["last_seen"], reverse=True)
    return out


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
    return {"ok": True}


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

    fp = fingerprint_of(request, device)
    ua = request.headers.get("user-agent", "")[:250]
    ip = client_ip(request)

    dev = db.scalar(
        select(Device).where(Device.friend_id == friend.id, Device.fingerprint == fp)
    )
    if dev is None:
        existing = db.scalar(
            select(func.count(Device.id)).where(Device.friend_id == friend.id)
        )
        if friend.device_limit and existing >= friend.device_limit:
            raise HTTPException(403, "设备数已达上限")
        dev = Device(
            friend_id=friend.id, fingerprint=fp, user_agent=ua, last_ip=ip, fetch_count=0
        )
        db.add(dev)
        db.flush()

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

    uris = [
        n.uri
        for n in db.scalars(
            select(Node)
            .join(Allocation, Allocation.node_id == Node.id)
            .where(Allocation.friend_id == friend.id, Node.enabled.is_(True))
            .order_by(Node.sort_order, Node.id)
        ).all()
    ]
    db.commit()

    if not uris:
        raise HTTPException(404, "尚未分配任何节点")

    try:
        body, content_type = render(uris, target)
    except ValueError as exc:
        raise HTTPException(400, str(exc))

    headers = {
        "profile-update-interval": "12",
        "content-disposition": f'attachment; filename="{friend.uid}.yaml"'
        if target.startswith("clash")
        else f'attachment; filename="{friend.uid}.txt"',
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
    return {"ok": True}
