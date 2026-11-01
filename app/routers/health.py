from datetime import UTC, datetime

from fastapi import APIRouter

from app.schemas import HealthOut, to_iso_z

router = APIRouter(tags=["health"])


@router.get("/", response_model=HealthOut)
async def health_check() -> HealthOut:
    return HealthOut(current_time=to_iso_z(datetime.now(UTC)))
