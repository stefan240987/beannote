"""BeanNote FastAPI app: JWT auth, OAuth, scan, beans, ratings, PWA."""

from __future__ import annotations

import base64
import os
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlencode

import httpx
import jwt
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from db import (
    VERSION,
    ENVIRONMENT,
    authenticate_email,
    create_email_user,
    distinct_values,
    export_ratings,
    get_bean,
    get_db_path,
    get_flavor_profile,
    get_images_dir,
    get_user,
    init_db,
    insert_bean,
    insert_rating,
    list_beans,
    resolve_image_path,
    toggle_favorite,
    save_bean_image,
    should_auto_flush,
    upsert_oauth_user,
)
from ocr import (
    compare_flavor_notes,
    encode_scan_jpeg,
    ensure_local_env,
    flavor_notes_for,
    load_local_env,
    processes_for,
    roast_levels_for,
    scan_available,
    scan_label,
)
from translations import LANGS, STRINGS, t

UI_LANGS = {"da": LANGS["da"], "en": LANGS["en"]}

ensure_local_env()
load_local_env()

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
COOKIE_NAME = "beannote_session"
OAUTH_STATE_COOKIE = "beannote_oauth"
JWT_ALG = "HS256"
TOKEN_DAYS = 14
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


def _cookie_secure() -> bool:
    return ENVIRONMENT == "production"


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


def _set_session(response: Response, user: dict[str, Any]) -> str:
    token = _issue_token(user)
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        secure=_cookie_secure(),
        samesite="lax",
        max_age=TOKEN_DAYS * 24 * 60 * 60,
        path="/",
    )
    return token


def _clear_session(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")
    response.delete_cookie(OAUTH_STATE_COOKIE, path="/")


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
    if ENVIRONMENT != "local":
        raise _auth_error("oauth_unavailable")
    profile = _local_test_profile(provider)
    return upsert_oauth_user(
        email=profile["email"],
        username=profile["username"],
        provider=provider,
        oauth_id=profile["oauth_id"],
    )


def _local_oauth_redirect(provider: str) -> RedirectResponse:
    user = _local_oauth_user(provider)
    dest = RedirectResponse("/")
    _set_session(dest, user)
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


def _lan_ipv4() -> str:
    import socket

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


def _print_lan_banner() -> None:
    ip = _lan_ipv4()
    url = f"http://{ip}:8501"
    width = max(56, len(url) + 8)
    bar = "─" * width
    print()
    print(f"┌{bar}┐")
    print(f"│  BeanNote on your phone{' ' * (width - 24)}│")
    print(f"│  {url}{' ' * (width - len(url) - 2)}│")
    print(f"│  http://127.0.0.1:8501{' ' * (width - 24)}│")
    print(f"│  Scan the QR code, or open the LAN URL{' ' * (width - 40)}│")
    print(f"└{bar}┘")
    try:
        import qrcode

        qr = qrcode.QRCode(border=1)
        qr.add_data(url)
        qr.make(fit=True)
        qr.print_ascii(invert=True)
    except Exception:
        print("  (install qrcode for a terminal QR code)")
    print()


app = FastAPI(title="BeanNote", version=VERSION)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
if STATIC.is_dir():
    app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.on_event("startup")
def _startup() -> None:
    ensure_local_env()
    load_local_env()
    init_db()
    if ENVIRONMENT == "local":
        _print_lan_banner()


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "version": VERSION,
        "environment": ENVIRONMENT,
        "auto_flush": should_auto_flush(),
        "scan": scan_available(),
        "db": str(get_db_path()),
    }


@app.get("/api/config")
def config(request: Request, user: Optional[dict[str, Any]] = Depends(optional_user)) -> dict[str, Any]:
    lang = (request.query_params.get("lang") or "da").lower()
    if lang not in UI_LANGS:
        lang = "da"
    local = ENVIRONMENT == "local"
    return {
        "version": VERSION,
        "lang": lang,
        "langs": UI_LANGS,
        "strings": STRINGS.get(lang) or STRINGS["en"],
        "i18n": {code: STRINGS[code] for code in UI_LANGS},
        "user": user,
        "environment": ENVIRONMENT,
        "local_dev": local,
        "providers": {
            "google": local or _oauth_configured("google"),
            "apple": local or _oauth_configured("apple"),
        },
        "brew_methods": BREW_METHODS,
        "processes": processes_for(lang),
        "roast_levels": roast_levels_for(lang),
        "flavor_notes": flavor_notes_for(lang),
        "origins": distinct_values("origin"),
        "roasts": distinct_values("roast_level"),
    }


class EmailAuthIn(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=128)
    username: str = ""


class BeanIn(BaseModel):
    name: str
    roaster: str
    origin: str = ""
    process: str = ""
    roast_level: str = ""
    roaster_notes: str = ""
    flavor_tags: list[str] = Field(default_factory=list)
    story: str = ""
    image_url: str = ""
    recommended_method: str = ""
    grind_size: str = ""
    water_temp: str = ""
    brew_ratio: str = ""
    brew_recommendation: dict[str, Any] = Field(default_factory=dict)
    roast_date: str = ""
    altitude: str = ""
    varietal: str = ""
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    region_full: str = ""
    skip_fuzzy: bool = False


class RatingIn(BaseModel):
    bean_id: int
    brew_method: str = "V60"
    rating: float = 4.0
    acidity: float = 3.0
    sweetness: float = 3.0
    body: float = 3.0
    aftertaste: float = 3.0
    notes: str = ""
    grind_setting: str = ""
    coffee_grams: Optional[float] = None
    water_grams: Optional[float] = None
    brew_time: str = ""


def _auth_error(code: str) -> HTTPException:
    mapping = {
        "invalid_email": 400,
        "password_too_short": 400,
        "email_taken": 409,
        "invalid_credentials": 401,
        "oauth_unavailable": 503,
    }
    return HTTPException(status_code=mapping.get(code, 400), detail=code)


@app.post("/api/auth/register")
def register(payload: EmailAuthIn, response: Response) -> dict[str, Any]:
    try:
        user = create_email_user(payload.email, payload.password, payload.username)
    except ValueError as exc:
        raise _auth_error(str(exc)) from exc
    token = _set_session(response, user)
    return {"user": user, "token": token}


@app.post("/api/auth/login")
def login(payload: EmailAuthIn, response: Response) -> dict[str, Any]:
    user = authenticate_email(payload.email, payload.password)
    if not user:
        raise _auth_error("invalid_credentials")
    token = _set_session(response, user)
    return {"user": user, "token": token}


@app.post("/api/auth/logout")
def logout(response: Response) -> dict[str, bool]:
    _clear_session(response)
    return {"ok": True}


@app.get("/api/auth/me")
def me(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    return {"user": user}


@app.post("/api/auth/{provider}/dev")
def oauth_dev(provider: str, response: Response) -> dict[str, Any]:
    """Instant local test sign-in. Production always uses real OAuth client IDs."""
    user = _local_oauth_user(provider)
    token = _set_session(response, user)
    return {"user": user, "token": token}


@app.get("/api/auth/google")
def google_start(request: Request) -> RedirectResponse:
    if ENVIRONMENT == "local":
        return _local_oauth_redirect("google")
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
        secure=_cookie_secure(),
        samesite="lax",
        max_age=600,
        path="/",
    )
    return dest


@app.get("/api/auth/google/callback")
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
    _set_session(dest, user)
    dest.delete_cookie(OAUTH_STATE_COOKIE, path="/")
    return dest


@app.get("/api/auth/apple")
def apple_start(request: Request) -> RedirectResponse:
    if ENVIRONMENT == "local":
        return _local_oauth_redirect("apple")
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
        secure=_cookie_secure(),
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
        return RedirectResponse("/?auth_error=oauth")
    state = form.get("state") or ""
    cookie_state = request.cookies.get(OAUTH_STATE_COOKIE) or ""
    if not state or state != cookie_state:
        raise HTTPException(status_code=400, detail="oauth_state")
    _read_oauth_state(state, "apple")
    identity = form.get("id_token") or ""
    if not identity:
        raise HTTPException(status_code=400, detail="oauth_code")
    claims = _verify_apple_identity(identity)
    user = upsert_oauth_user(
        email=claims.get("email") or "",
        username=form.get("user_name") or "",
        provider="apple",
        oauth_id=str(claims.get("sub") or ""),
    )
    dest = RedirectResponse("/")
    _set_session(dest, user)
    dest.delete_cookie(OAUTH_STATE_COOKIE, path="/")
    return dest


@app.get("/api/auth/apple/callback")
def apple_callback_get(request: Request) -> RedirectResponse:
    return _apple_finish(request, dict(request.query_params))


@app.post("/api/auth/apple/callback")
async def apple_callback_post(request: Request) -> RedirectResponse:
    form = await request.form()
    payload = {str(key): str(value) for key, value in form.items()}
    user_blob = payload.get("user") or ""
    if user_blob:
        try:
            import json

            parsed = json.loads(user_blob)
            name = parsed.get("name") or {}
            payload["user_name"] = " ".join(
                part for part in (name.get("firstName"), name.get("lastName")) if part
            ).strip()
        except json.JSONDecodeError:
            pass
    return _apple_finish(request, payload)


@app.get("/api/beans")
def beans(
    search: str = "",
    origin: str = "",
    roast_level: str = "",
    min_rating: float = 0.0,
    favorites: bool = False,
    user: dict[str, Any] = Depends(current_user),
) -> list[dict[str, Any]]:
    return list_beans(
        search=search,
        origin=origin,
        roast_level=roast_level,
        min_rating=min_rating,
        user_id=user["id"],
        favorites_only=favorites,
    )


@app.get("/api/beans/{bean_id}")
def bean_detail(bean_id: int, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    profile = get_flavor_profile(bean_id, user_id=user["id"])
    if not profile:
        raise HTTPException(status_code=404, detail="not_found")
    notes = compare_flavor_notes(
        (profile["bean"] or {}).get("roaster_notes") or "",
        ((profile.get("user") or {}) or {}).get("notes") or "",
        (profile["bean"] or {}).get("flavor_tags") or [],
    )
    profile["notes"] = notes
    return profile


@app.post("/api/beans")
def create_bean(payload: BeanIn, _user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    try:
        result = insert_bean(
            name=payload.name,
            roaster=payload.roaster,
            origin=payload.origin,
            process=payload.process,
            roast_level=payload.roast_level,
            roaster_notes=payload.roaster_notes,
            flavor_tags=payload.flavor_tags,
            skip_fuzzy=payload.skip_fuzzy,
            image_url=payload.image_url,
            story=payload.story,
            recommended_method=payload.recommended_method,
            grind_size=payload.grind_size,
            water_temp=payload.water_temp,
            brew_ratio=payload.brew_ratio,
            brew_recommendation=payload.brew_recommendation,
            roast_date=payload.roast_date,
            altitude=payload.altitude,
            varietal=payload.varietal,
            latitude=payload.latitude,
            longitude=payload.longitude,
            region_full=payload.region_full,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result


@app.post("/api/beans/{bean_id}/favorite")
def favorite_bean(bean_id: int, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    if not get_bean(bean_id):
        raise HTTPException(status_code=404, detail="not_found")
    return {"is_favorite": toggle_favorite(user["id"], bean_id), "bean_id": bean_id}


@app.post("/api/scan")
async def scan(
    request: Request,
    file: UploadFile = File(...),
    lang: str = Form(""),
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    del user
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="empty_image")
    if not scan_available():
        raise HTTPException(status_code=503, detail="ocr_missing")
    chosen = (lang or request.query_params.get("lang") or "da").lower().strip()
    if chosen not in LANGS:
        chosen = "da"
    try:
        jpeg = encode_scan_jpeg(raw)
        parsed = scan_label(jpeg, lang=chosen)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=422, detail="ocr_fail") from exc
    image_url = save_bean_image(jpeg, filename="scan.jpg")
    parsed["image_url"] = image_url
    parsed["preview"] = "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii")
    return parsed


@app.post("/api/ratings")
def create_rating(payload: RatingIn, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    if not get_bean(payload.bean_id):
        raise HTTPException(status_code=404, detail="not_found")
    rating = insert_rating(
        bean_id=payload.bean_id,
        brew_method=payload.brew_method,
        rating=payload.rating,
        acidity=payload.acidity,
        sweetness=payload.sweetness,
        body=payload.body,
        aftertaste=payload.aftertaste,
        notes=payload.notes,
        user_id=user["id"],
        grind_setting=payload.grind_setting,
        coffee_grams=payload.coffee_grams,
        water_grams=payload.water_grams,
        brew_time=payload.brew_time,
    )
    return {"rating": rating, "profile": get_flavor_profile(payload.bean_id, user_id=user["id"])}


@app.get("/api/export")
def export_log(fmt: str = "csv", _user: dict[str, Any] = Depends(current_user)) -> Response:
    filename, mime, payload = export_ratings(fmt)
    return Response(
        content=payload,
        media_type=mime,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/i18n")
def i18n_pack() -> dict[str, dict[str, str]]:
    return {code: STRINGS[code] for code in UI_LANGS}


@app.get("/api/i18n/{lang}")
def i18n(lang: str) -> dict[str, str]:
    code = (lang or "da").lower()
    if code not in UI_LANGS:
        code = "da"
    return STRINGS.get(code) or STRINGS["en"]


@app.get("/media/{image_name}")
def media(image_name: str) -> FileResponse:
    safe = Path(image_name).name
    path = resolve_image_path(f"images/{safe}") or (get_images_dir() / safe)
    if not path or not path.is_file():
        raise HTTPException(status_code=404, detail="not_found")
    return FileResponse(path)


@app.get("/manifest.webmanifest")
def manifest() -> FileResponse:
    return FileResponse(STATIC / "manifest.webmanifest", media_type="application/manifest+json")


@app.get("/sw.js")
def service_worker() -> FileResponse:
    return FileResponse(STATIC / "sw.js", media_type="text/javascript")


@app.get("/")
def index() -> FileResponse:
    headers = {}
    if ENVIRONMENT != "production":
        headers["Cache-Control"] = "no-store, max-age=0"
    return FileResponse(STATIC / "index.html", headers=headers)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8501,
        reload=ENVIRONMENT != "production",
    )


@app.exception_handler(HTTPException)
async def http_error(_request: Request, exc: HTTPException) -> JSONResponse:
    detail = exc.detail if isinstance(exc.detail, str) else "error"
    return JSONResponse({"detail": detail, "message": t("da", detail) if detail in STRINGS["da"] else detail}, status_code=exc.status_code)
