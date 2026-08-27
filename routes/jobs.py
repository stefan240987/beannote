"""Job status polling. Long OCR/enrich work never runs on this path."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from deps import current_user
from jobs import get_job, public_job

router = APIRouter(tags=["jobs"])


@router.get("/api/jobs/{job_id}")
def job_status(job_id: int, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    job = get_job(job_id, user_id=user["id"])
    if not job:
        raise HTTPException(status_code=404, detail="not_found")
    return public_job(job)
