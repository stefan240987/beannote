"""BeanNote FastAPI composition root: middleware, static PWA, router includes."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from db import ENVIRONMENT, VERSION, get_images_dir, init_db, record_api_hit, resolve_catalog_image, resolve_image_path
from deps import (
    STATIC,
    analytics_ids,
    attach_analytics_cookies,
    current_user,
    is_public_api_path,
    optional_user,
)
from ocr import ensure_local_env, load_local_env
from routes.admin import router as admin_router
from routes.auth import router as auth_router
from routes.beans import explore_router, router as beans_router
from routes.brews import router as brews_router
from routes.gear import router as gear_router
from routes.jobs import router as jobs_router
from routes.meta import router as meta_router
from routes.scan import router as scan_router
from translations import STRINGS, t

ensure_local_env()
load_local_env()


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
    url = f"http://{ip}:8502"
    local_line = "Local:          http://localhost:8502"
    net_line = f"Network/Mobile: {url}"
    width = max(56, len(local_line) + 8, len(net_line) + 8)
    bar = "─" * width
    print()
    print(f"┌{bar}┐")
    print(f"│  BeanNote on your phone{' ' * (width - 24)}│")
    print(f"│  {local_line}{' ' * (width - len(local_line) - 2)}│")
    print(f"│  {net_line}{' ' * (width - len(net_line) - 2)}│")
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
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")


@app.middleware("http")
async def require_api_auth(request: Request, call_next):
    """Reject unauthenticated /api requests except Explore, config, i18n, health, and auth."""
    if request.method != "OPTIONS" and request.url.path.startswith("/api/"):
        if not is_public_api_path(request.url.path):
            try:
                request.state.user = current_user(request)
            except HTTPException as exc:
                if exc.status_code in {401, 403}:
                    detail = exc.detail if isinstance(exc.detail, str) else "auth_required"
                    return JSONResponse(
                        {
                            "detail": detail,
                            "message": t("da", detail) if detail in STRINGS["da"] else detail,
                        },
                        status_code=exc.status_code,
                    )
                raise
    return await call_next(request)


@app.middleware("http")
async def track_usage(request: Request, call_next):
    """Attach visitor cookies and log API status codes for the admin dashboard."""
    path = request.url.path or "/"
    skip = (
        path.startswith("/static")
        or path.startswith("/media")
        or path in {"/sw.js", "/manifest.webmanifest"}
        or request.method == "OPTIONS"
    )
    visitor = session = ""
    new_visitor = new_session = False
    if not skip:
        visitor, session, new_visitor, new_session = analytics_ids(request)
    response = await call_next(request)
    if skip:
        return response
    try:
        attach_analytics_cookies(response, request, visitor, session, new_visitor, new_session)
        user = getattr(request.state, "user", None)
        if user is None:
            user = optional_user(request)
        is_admin = bool(user and user.get("is_admin"))
        if path.startswith("/api/") and not is_admin:
            record_api_hit(path, int(response.status_code))
    except Exception:
        pass
    return response

_CATALOG_MEDIA = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
    "gif": "image/gif",
    "svg": "image/svg+xml",
}


def _catalog_file_response(kind: str, name: str) -> FileResponse:
    path = resolve_catalog_image(kind, name)
    if not path:
        raise HTTPException(status_code=404, detail="not_found")
    media = _CATALOG_MEDIA.get(path.suffix.lower().lstrip("."), "application/octet-stream")
    return FileResponse(path, media_type=media)


@app.get("/static/img/beans/{name}")
def catalog_bean_image(name: str) -> FileResponse:
    return _catalog_file_response("beans", name)


@app.get("/static/img/gear/{name}")
def catalog_gear_image(name: str) -> FileResponse:
    return _catalog_file_response("gear", name)


if STATIC.is_dir():
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

app.include_router(admin_router)
app.include_router(auth_router)
app.include_router(explore_router)
app.include_router(beans_router)
app.include_router(scan_router)
app.include_router(jobs_router)
app.include_router(brews_router)
app.include_router(gear_router)
app.include_router(meta_router)


@app.on_event("startup")
def _startup() -> None:
    if ENVIRONMENT == "production" and not (os.getenv("JWT_SECRET") or "").strip():
        raise RuntimeError("JWT_SECRET is required when ENVIRONMENT=production")
    ensure_local_env()
    load_local_env()
    init_db()
    from worker import start_embedded_worker

    start_embedded_worker()
    if ENVIRONMENT == "local":
        _print_lan_banner()


@app.exception_handler(HTTPException)
async def http_error(_request: Request, exc: HTTPException) -> JSONResponse:
    detail = exc.detail if isinstance(exc.detail, str) else "error"
    return JSONResponse(
        {"detail": detail, "message": t("da", detail) if detail in STRINGS["da"] else detail},
        status_code=exc.status_code,
    )


@app.get("/media/{image_name}")
def media(image_name: str) -> FileResponse:
    safe = Path(image_name).name
    path = resolve_image_path(f"images/{safe}") or (get_images_dir() / safe)
    if not path or not path.is_file():
        raise HTTPException(status_code=404, detail="not_found")
    return FileResponse(path)


@app.get("/manifest.webmanifest")
def manifest() -> FileResponse:
    return FileResponse(
        STATIC / "manifest.webmanifest",
        media_type="application/manifest+json",
        headers={"Cache-Control": "no-store, max-age=0"},
    )


def _with_version(text: str) -> str:
    return text.replace("__BN_VERSION__", VERSION)


@app.get("/sw.js")
def service_worker() -> Response:
    return Response(
        _with_version((STATIC / "sw.js").read_text(encoding="utf-8")),
        media_type="text/javascript",
        headers={"Cache-Control": "no-store, max-age=0"},
    )


def _spa_index() -> HTMLResponse:
    return HTMLResponse(
        _with_version((STATIC / "index.html").read_text(encoding="utf-8")),
        headers={"Cache-Control": "no-store, max-age=0"},
    )


@app.get("/")
def index() -> FileResponse:
    return _spa_index()


@app.get("/admin")
def admin_spa(request: Request):
    try:
        user = current_user(request)
    except HTTPException:
        return RedirectResponse("/login?next=/admin", status_code=303)
    if not user.get("is_admin"):
        return RedirectResponse("/explore", status_code=303)
    return _spa_index()


_SPA_PATHS = (
    "/explore",
    "/login",
    "/register",
    "/signup",
    "/favorites",
    "/scan",
    "/diary",
    "/profile",
)
for _spa_path in _SPA_PATHS:
    app.add_api_route(_spa_path, _spa_index, methods=["GET"], include_in_schema=False)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8502,
        reload=ENVIRONMENT != "production",
    )
