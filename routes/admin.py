"""Admin analytics APIs and public pageview beacon."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, Query, Request, Response

from db import admin_analytics, list_admin_users, record_pageview, set_user_blocked
from deps import (
    _auth_error,
    analytics_ids,
    attach_analytics_cookies,
    optional_user,
    require_admin,
)
from schemas import PageviewIn

router = APIRouter(tags=["admin"])


@router.post("/api/analytics/pageview")
def track_pageview(
    payload: PageviewIn,
    request: Request,
    response: Response,
    user: Optional[dict[str, Any]] = Depends(optional_user),
) -> dict[str, bool]:
    visitor, session, new_visitor, new_session = analytics_ids(request)
    if not (user and user.get("is_admin")):
        record_pageview(
            payload.path,
            visitor_id=visitor,
            session_id=session,
            user_id=int(user["id"]) if user else None,
        )
    attach_analytics_cookies(response, request, visitor, session, new_visitor, new_session)
    return {"ok": True}


@router.get("/api/admin/analytics")
def get_admin_analytics(
    days: int = Query(default=30, ge=1, le=90),
    _admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    del _admin
    return admin_analytics(days)


@router.get("/api/admin/users")
def get_admin_users(
    q: str = "",
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=10, ge=1, le=50),
    _admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    del _admin
    return list_admin_users(q, page, per_page)


@router.post("/api/admin/users/{user_id}/block")
def block_admin_user(
    user_id: int,
    admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    try:
        user = set_user_blocked(int(admin["id"]), user_id, True)
    except ValueError as exc:
        raise _auth_error(str(exc)) from exc
    return {"user": user}


@router.post("/api/admin/users/{user_id}/unblock")
def unblock_admin_user(
    user_id: int,
    admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    try:
        user = set_user_blocked(int(admin["id"]), user_id, False)
    except ValueError as exc:
        raise _auth_error(str(exc)) from exc
    return {"user": user}
