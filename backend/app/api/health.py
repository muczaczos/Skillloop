from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{"status": "ok"}]})

    status: Literal["ok"]


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Liveness check",
)
def health() -> HealthResponse:
    """Reports that the application process is running and answering requests.

    No authentication is required. The database is deliberately NOT checked: the application
    starts in degraded mode when the database is unreachable, so this endpoint can answer 200
    while endpoints that need the database answer 503. Used by developers, Docker and future
    deployments.
    """
    return HealthResponse(status="ok")
