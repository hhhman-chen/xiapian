"""试用登录：用户表 + 签名 Cookie。"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse

ROOT = Path(__file__).resolve().parent.parent.parent
USERS_PATH = Path(os.environ.get("USERS_FILE", str(ROOT / "users.json")))
AUTH_SECRET = os.environ.get("AUTH_SECRET", "xiapian-dev-secret-change")
COOKIE = "xiapian_session"
TTL = 14 * 24 * 3600

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _hash(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 120000).hex()


def load_users() -> list[dict[str, Any]]:
    if not USERS_PATH.exists():
        return []
    data = json.loads(USERS_PATH.read_text(encoding="utf-8"))
    return data.get("users", data if isinstance(data, list) else [])


def save_users(users: list[dict[str, Any]]) -> None:
    USERS_PATH.parent.mkdir(parents=True, exist_ok=True)
    USERS_PATH.write_text(json.dumps({"users": users}, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(USERS_PATH, 0o600)


def upsert_user(username: str, password: str, name: str, role: str = "trial") -> None:
    users = load_users()
    salt = secrets.token_hex(8)
    rec = {"username": username, "name": name, "role": role, "salt": salt, "hash": _hash(password, salt)}
    users = [u for u in users if u.get("username") != username] + [rec]
    save_users(users)


def verify(username: str, password: str) -> Optional[dict[str, Any]]:
    for u in load_users():
        if u.get("username") != username:
            continue
        if hmac.compare_digest(u.get("hash", ""), _hash(password, u.get("salt", ""))):
            return u
    return None


def _sign(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
    import base64
    body = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    sig = hmac.new(AUTH_SECRET.encode(), body.encode(), hashlib.sha256).hexdigest()[:40]
    return f"{body}.{sig}"


def _unsign(token: str) -> Optional[dict[str, Any]]:
    try:
        import base64
        body, sig = token.split(".", 1)
        expect = hmac.new(AUTH_SECRET.encode(), body.encode(), hashlib.sha256).hexdigest()[:40]
        if not hmac.compare_digest(sig, expect):
            return None
        pad = "=" * (-len(body) % 4)
        data = json.loads(base64.urlsafe_b64decode(body + pad))
        if data.get("exp", 0) < time.time():
            return None
        return data
    except Exception:
        return None


def current_user(request: Request) -> Optional[dict[str, Any]]:
    token = request.cookies.get(COOKIE)
    if not token:
        return None
    return _unsign(token)


def require_user(request: Request) -> dict[str, Any]:
    user = current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="请先登录")
    return user


@router.post("/login")
async def login(payload: dict[str, Any], request: Request) -> JSONResponse:
    username = str(payload.get("username") or "").strip()
    password = str(payload.get("password") or "")
    user = verify(username, password)
    if not user:
        raise HTTPException(status_code=401, detail="账号或密码不对")
    token = _sign({
        "u": user["username"],
        "n": user.get("name") or user["username"],
        "r": user.get("role") or "trial",
        "exp": int(time.time()) + TTL,
    })
    proto = (request.headers.get("x-forwarded-proto") or request.url.scheme or "").split(",")[0].strip()
    body = {"username": user["username"], "name": user.get("name"), "role": user.get("role")}
    resp = JSONResponse(body)
    resp.set_cookie(
        COOKIE, token, httponly=True, samesite="lax",
        max_age=TTL, path="/", secure=(proto == "https"),
    )
    return resp


@router.post("/logout")
async def logout(response: Response) -> dict[str, str]:
    response.delete_cookie(COOKIE, path="/")
    return {"ok": "1"}


@router.get("/me")
async def me(request: Request) -> dict[str, Any]:
    user = current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="未登录")
    return {"username": user.get("u"), "name": user.get("n"), "role": user.get("r")}
