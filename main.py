"""BeanNote FastAPI composition root: middleware, static PWA, router includes."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from db import ENVIRONMENT, VERSION, get_images_dir, init_db, resolve_image_path
from deps import STATIC
from ocr import ensure_local_env, load_local_env
from routes.auth import router as auth_router
from routes.beans import router as beans_router
from routes.brews import router as brews_router
from routes.gear import router as gear_router
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

app.include_router(auth_router)
app.include_router(beans_router)
app.include_router(scan_router)
app.include_router(brews_router)
app.include_router(gear_router)
app.include_router(meta_router)


@app.on_event("startup")
def _startup() -> None:
    ensure_local_env()
    load_local_env()
    init_db()
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
    return FileResponse(STATIC / "manifest.webmanifest", media_type="application/manifest+json")


@app.get("/sw.js")
def service_worker() -> FileResponse:
    return FileResponse(
        STATIC / "sw.js",
        media_type="text/javascript",
        headers={"Cache-Control": "no-store, max-age=0"},
    )


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
