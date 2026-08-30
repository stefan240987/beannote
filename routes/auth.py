"""Auth routes: email login, JWT session, Google/Apple OAuth."""

from __future__ import annotations

import json
import os
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse

from db import authenticate_email, create_email_user, is_local_dev, upsert_oauth_user
from deps import (
    OAUTH_STATE_COOKIE,
    _auth_error,
    _cookie_secure,
    _local_oauth_redirect,
    _local_oauth_user,
    _oauth_configured,
    _public_base,
    _read_oauth_state,
    _set_session,
    _sign_oauth_state,
    _clear_session,
    current_user,
)
from schemas import EmailAuthIn

router = APIRouter(prefix="/api/auth", tags=["auth"])
_OAUTH_FAIL = "/?auth_error=oauth"


def _oauth_failed() -> RedirectResponse:
    """Send the user back to the login screen after a failed provider callback."""
    return RedirectResponse(_OAUTH_FAIL, status_code=303)


def _oauth_home() -> RedirectResponse:
    return RedirectResponse("/", status_code=303)


@router.post("/register")
def register(payload: EmailAuthIn, request: Request, response: Response) -> dict[str, Any]:
    try:
        user = create_email_user(payload.email, payload.password, payload.username)
    except ValueError as exc:
        raise _auth_error(str(exc)) from exc
    token = _set_session(response, user, request)
    return {"user": user, "token": token}


@router.post("/login")
def login(payload: EmailAuthIn, request: Request, response: Response) -> dict[str, Any]:
    user = authenticate_email(payload.email, payload.password)
    if not user:
        raise _auth_error("invalid_credentials")
    token = _set_session(response, user, request)
    return {"user": user, "token": token}


@router.post("/logout")
def logout(request: Request, response: Response) -> dict[str, bool]:
    _clear_session(response, request)
    return {"ok": True}


@router.get("/me")
def me(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    return {"user": user}


@router.post("/{provider}/dev")
def oauth_dev(provider: str, request: Request, response: Response) -> dict[str, Any]:
    """Instant local test sign-in. Production always uses real OAuth client IDs."""
    user = _local_oauth_user(provider)
    token = _set_session(response, user, request)
    return {"user": user, "token": token}


@router.get("/google")
def google_start(request: Request) -> RedirectResponse:
    if is_local_dev():
        return _local_oauth_redirect("google", request)
    if not _oauth_configured("google"):
        raise _auth_error("oauth_unavailable")
    state = _sign_oauth_state("google")
    params = {
        "client_id": os.environ["GOOGLE_CLIENT_ID"],
        "redirect_uri": f"{_public_base(request)}/api/auth/google/callback",
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "access_type": "online",
        "prompt": "select_account",
    }
    dest = RedirectResponse(f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}")
    dest.set_cookie(
        OAUTH_STATE_COOKIE,
        state,
        httponly=True,
        secure=_cookie_secure(request),
        samesite="lax",
        max_age=600,
        path="/",
    )
    return dest


@router.get("/google/callback")
def google_callback(request: Request) -> RedirectResponse:
    if request.query_params.get("error"):
        return RedirectResponse("/?auth_error=oauth")
    state = request.query_params.get("state") or ""
    cookie_state = request.cookies.get(OAUTH_STATE_COOKIE) or ""
    if not state or state != cookie_state:
        raise HTTPException(status_code=400, detail="oauth_state")
    _read_oauth_state(state, "google")
    code = request.query_params.get("code") or ""
    if not code:
        raise HTTPException(status_code=400, detail="oauth_code")
    try:
        token_data = httpx.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": os.environ["GOOGLE_CLIENT_ID"],
                "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
                "redirect_uri": f"{_public_base(request)}/api/auth/google/callback",
                "grant_type": "authorization_code",
            },
            timeout=20,
        )
        token_data.raise_for_status()
        access = token_data.json().get("access_token")
        info = httpx.get(
            "https://openidconnect.googleapis.com/v1/userinfo",
            headers={"Authorization": f"Bearer {access}"},
            timeout=20,
        )
        info.raise_for_status()
        profile = info.json()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="oauth_unavailable") from exc
    user = upsert_oauth_user(
        email=profile.get("email") or "",
        username=profile.get("name") or profile.get("given_name") or "",
        provider="google",
        oauth_id=str(profile.get("sub") or ""),
    )
    dest = RedirectResponse("/")
    _set_session(dest, user, request)
    dest.delete_cookie(
        OAUTH_STATE_COOKIE,
        path="/",
        httponly=True,
        samesite="lax",
        secure=_cookie_secure(request),
    )
    return dest


@router.get("/apple")
def apple_start(request: Request) -> RedirectResponse:
    if is_local_dev():
        return _local_oauth_redirect("apple", request)
    if not _oauth_configured("apple"):
        raise _auth_error("oauth_unavailable")
    state = _sign_oauth_state("apple")
    params = {
        "client_id": os.environ["APPLE_CLIENT_ID"],
        "redirect_uri": f"{_public_base(request)}/api/auth/apple/callback",
        "response_type": "code id_token",
        "response_mode": "form_post",
        "scope": "name email",
        "state": state,
    }
    dest = RedirectResponse(f"https://appleid.apple.com/auth/authorize?{urlencode(params)}")
    dest.set_cookie(
        OAUTH_STATE_COOKIE,
        state,
        httponly=True,
        secure=_cookie_secure(request),
        samesite="lax",
        max_age=600,
        path="/",
    )
    return dest


def _verify_apple_identity(identity: str) -> dict[str, Any]:
    jwks = jwt.PyJWKClient("https://appleid.apple.com/auth/keys")
    try:
        key = jwks.get_signing_key_from_jwt(identity)
        return jwt.decode(
            identity,
            key.key,
            algorithms=["RS256"],
            audience=os.environ["APPLE_CLIENT_ID"],
            issuer="https://appleid.apple.com",
        )
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status_code=400, detail="oauth_token") from exc


def _apple_finish(request: Request, form: dict[str, str]) -> RedirectResponse:
    if form.get("error"):
        return _oauth_failed()
    state = form.get("state") or ""
    if not state:
        return _oauth_failed()
    cookie_state = request.cookies.get(OAUTH_STATE_COOKIE) or ""
    if cookie_state and state != cookie_state:
        return _oauth_failed()
    try:
        _read_oauth_state(state, "apple")
    except HTTPException:
        return _oauth_failed()
    identity = form.get("id_token") or ""
    if not identity:
        return _oauth_failed()
    try:
        claims = _verify_apple_identity(identity)
        user = upsert_oauth_user(
            email=claims.get("email") or "",
            username=form.get("user_name") or "",
            provider="apple",
            oauth_id=str(claims.get("sub") or ""),
        )
    except (HTTPException, ValueError):
        return _oauth_failed()
    dest = _oauth_home()
    _set_session(dest, user, request)
    dest.delete_cookie(
        OAUTH_STATE_COOKIE,
        path="/",
        httponly=True,
        samesite="lax",
        secure=_cookie_secure(request),
    )
    return dest


@router.get("/apple/callback")
def apple_callback_get(request: Request) -> RedirectResponse:
    return _apple_finish(request, dict(request.query_params))


@router.post("/apple/callback")
async def apple_callback_post(request: Request) -> RedirectResponse:
    form = await request.form()
    payload = {str(key): str(value) for key, value in form.items()}
    user_blob = payload.get("user") or ""
    if user_blob:
        try:
            parsed = json.loads(user_blob)
            name = parsed.get("name") or {}
            payload["user_name"] = " ".join(
                part for part in (name.get("firstName"), name.get("lastName")) if part
            ).strip()
        except json.JSONDecodeError:
            pass
    return _apple_finish(request, payload)
