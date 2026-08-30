"""Shared FastAPI dependencies: JWT sessions, OAuth helpers, support config."""

from __future__ import annotations

import os
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

import jwt
from fastapi import Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse

from db import ENVIRONMENT, RESET_DB_ON_START, get_user, is_local_dev, upsert_oauth_user
from translations import ui_langs

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
COOKIE_NAME = "beannote_session"
OAUTH_STATE_COOKIE = "beannote_oauth"
JWT_ALG = "HS256"
TOKEN_DAYS = 14
UI_LANGS = ui_langs()
BREW_METHODS = [
    "V60",
    "Espresso",
    "AeroPress",
    "Chemex",
    "French Press",
    "Kalita",
    "Batch Brew",
    "Moka",
    "Cold Brew",
]
LOCAL_SUPPORT_MOBILEPAY = "https://mobilepay.dk"
LOCAL_SUPPORT_BUYMEACOFFEE = "https://buymeacoffee.com"


def _https_url(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw)
    if parsed.scheme != "https" or not parsed.netloc:
        return ""
    return raw


def support_config() -> dict[str, Any]:
    """Docker/ENV-driven support links. Local/test gets dummy URLs so the modal can be QA'd."""
    mobilepay = _https_url(os.getenv("SUPPORT_MOBILEPAY_URL") or "")
    buymeacoffee = _https_url(os.getenv("SUPPORT_BUYMEACOFFEE_URL") or "")
    local_test = is_local_dev() or RESET_DB_ON_START
    test_mode = False
    if local_test and not (mobilepay and buymeacoffee):
        mobilepay = mobilepay or LOCAL_SUPPORT_MOBILEPAY
        buymeacoffee = buymeacoffee or LOCAL_SUPPORT_BUYMEACOFFEE
        test_mode = True
    return {
        "support_enabled": bool(mobilepay or buymeacoffee),
        "mobilepay_url": mobilepay,
        "buymeacoffee_url": buymeacoffee,
        "support_test_mode": test_mode,
    }


def _secret() -> str:
    secret = (os.getenv("JWT_SECRET") or "").strip()
    if secret:
        return secret
    if ENVIRONMENT == "production":
        raise RuntimeError("JWT_SECRET is required when ENVIRONMENT=production")
    return "beannote-local-dev-only"


def _public_base(request: Request | None = None) -> str:
    configured = (os.getenv("PUBLIC_BASE_URL") or "").strip().rstrip("/")
    if configured:
        return configured
    if request is not None:
        return str(request.base_url).rstrip("/")
    return "http://127.0.0.1:8501"


def _cookie_secure(request: Request | None = None) -> bool:
    """Secure cookies on HTTPS only, so Unraid LAN HTTP login still works."""
    if ENVIRONMENT != "production":
        return False
    public = (os.getenv("PUBLIC_BASE_URL") or "").strip().lower()
    if public.startswith("https://"):
        return True
    if public.startswith("http://"):
        return False
    if request is None:
        return False
    forwarded = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
    scheme = forwarded or (request.url.scheme or "").lower()
    return scheme == "https"


def _issue_token(user: dict[str, Any]) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user["id"]),
        "email": user.get("email") or "",
        "username": user.get("username") or "",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(days=TOKEN_DAYS)).timestamp()),
    }
    return jwt.encode(payload, _secret(), algorithm=JWT_ALG)


def _set_session(response: Response, user: dict[str, Any], request: Request | None = None) -> str:
    token = _issue_token(user)
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        secure=_cookie_secure(request),
        samesite="lax",
        max_age=TOKEN_DAYS * 24 * 60 * 60,
        path="/",
    )
    return token


def _clear_session(response: Response, request: Request | None = None) -> None:
    secure = _cookie_secure(request)
    response.delete_cookie(COOKIE_NAME, path="/", httponly=True, samesite="lax", secure=secure)
    response.delete_cookie(OAUTH_STATE_COOKIE, path="/", httponly=True, samesite="lax", secure=secure)


def _decode_token(token: str) -> dict[str, Any]:
    return jwt.decode(token, _secret(), algorithms=[JWT_ALG])


def _token_from_request(request: Request) -> str:
    header = request.headers.get("authorization") or request.headers.get("Authorization") or ""
    if header.lower().startswith("bearer "):
        return header.split(" ", 1)[1].strip()
    return (request.cookies.get(COOKIE_NAME) or "").strip()


def current_user(request: Request) -> dict[str, Any]:
    token = _token_from_request(request)
    if not token:
        raise HTTPException(status_code=401, detail="auth_required")
    try:
        payload = _decode_token(token)
        user = get_user(int(payload["sub"]))
    except (jwt.InvalidTokenError, KeyError, TypeError, ValueError):
        raise HTTPException(status_code=401, detail="auth_required") from None
    if not user:
        raise HTTPException(status_code=401, detail="auth_required")
    return user


def optional_user(request: Request) -> Optional[dict[str, Any]]:
    try:
        return current_user(request)
    except HTTPException:
        return None


def require_admin(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    if not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="forbidden")
    return user


def _oauth_configured(provider: str) -> bool:
    """Production OAuth is ready when real client IDs/secrets are present."""
    if provider == "google":
        return bool(os.getenv("GOOGLE_CLIENT_ID") and os.getenv("GOOGLE_CLIENT_SECRET"))
    if provider == "apple":
        return bool(
            os.getenv("APPLE_CLIENT_ID")
            and os.getenv("APPLE_TEAM_ID")
            and os.getenv("APPLE_KEY_ID")
            and (os.getenv("APPLE_PRIVATE_KEY") or os.getenv("APPLE_PRIVATE_KEY_PATH"))
        )
    return False


def _local_test_profile(provider: str) -> dict[str, str]:
    if provider == "google":
        return {
            "email": "google_test_user@beannote.local",
            "username": "Google Test",
            "oauth_id": "google-local-test",
        }
    if provider == "apple":
        return {
            "email": "apple_test_user@beannote.local",
            "username": "Apple Test",
            "oauth_id": "apple-local-test",
        }
    raise HTTPException(status_code=400, detail="invalid_oauth")


def _local_oauth_user(provider: str) -> dict[str, Any]:
    if not is_local_dev():
        raise _auth_error("oauth_unavailable")
    profile = _local_test_profile(provider)
    return upsert_oauth_user(
        email=profile["email"],
        username=profile["username"],
        provider=provider,
        oauth_id=profile["oauth_id"],
    )


def _local_oauth_redirect(provider: str, request: Request | None = None) -> RedirectResponse:
    user = _local_oauth_user(provider)
    dest = RedirectResponse("/")
    _set_session(dest, user, request)
    return dest


def _sign_oauth_state(provider: str) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "p": provider,
            "n": secrets.token_urlsafe(16),
            "exp": int((now + timedelta(minutes=10)).timestamp()),
        },
        _secret(),
        algorithm=JWT_ALG,
    )


def _read_oauth_state(raw: str, provider: str) -> None:
    try:
        payload = jwt.decode(raw, _secret(), algorithms=[JWT_ALG])
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status_code=400, detail="oauth_state") from exc
    if payload.get("p") != provider:
        raise HTTPException(status_code=400, detail="oauth_state")


def _apple_private_key() -> str:
    pem = (os.getenv("APPLE_PRIVATE_KEY") or "").replace("\\n", "\n").strip()
    if pem:
        return pem
    path = (os.getenv("APPLE_PRIVATE_KEY_PATH") or "").strip()
    if path and Path(path).is_file():
        return Path(path).read_text(encoding="utf-8")
    raise HTTPException(status_code=503, detail="oauth_unavailable")


def _apple_client_secret() -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "iss": os.environ["APPLE_TEAM_ID"],
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=15)).timestamp()),
            "aud": "https://appleid.apple.com",
            "sub": os.environ["APPLE_CLIENT_ID"],
        },
        _apple_private_key(),
        algorithm="ES256",
        headers={"kid": os.environ["APPLE_KEY_ID"]},
    )


def _auth_error(code: str) -> HTTPException:
    mapping = {
        "invalid_email": 400,
        "password_too_short": 400,
        "email_taken": 409,
        "invalid_credentials": 401,
        "oauth_unavailable": 503,
        "forbidden": 403,
        "name_roaster_taken": 409,
    }
    return HTTPException(status_code=mapping.get(code, 400), detail=code)
