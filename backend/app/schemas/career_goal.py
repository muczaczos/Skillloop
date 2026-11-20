"""API schemas of career goals (docs/API-DESIGN.md, section 6).

The status is never accepted from a client: a goal is created active and is archived through
its own endpoint, so `status`, `user_id` and `id` are rejected as unknown fields.
"""

from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from app.models.career_goal import GoalStatus, Seniority
from app.schemas.common import (
    GoalDescription,
    TargetRole,
    UtcDatetime,
    provided_fields,
    reject_null,
)


class CareerGoalCreate(BaseModel):
    """POST /career-goals."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "target_role": "DevOps Engineer",
                    "seniority": "junior",
                    "description": "Prepare for a Junior DevOps Engineer position.",
                }
            ]
        },
    )

    target_role: TargetRole
    seniority: Seniority
    description: GoalDescription = None


class CareerGoalUpdate(BaseModel):
    """PATCH /career-goals/{id}. A field that is left out is not changed; an explicit null
    (or blank text) clears description and is an error for target_role and seniority."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [{"seniority": "mid", "description": None}]},
    )

    target_role: TargetRole | None = None
    seniority: Seniority | None = None
    description: GoalDescription = None

    _required_fields_not_null = field_validator("target_role", "seniority", mode="before")(
        reject_null
    )

    @model_validator(mode="after")
    def _at_least_one_field(self) -> "CareerGoalUpdate":
        if not self.model_fields_set:
            raise ValueError("at least one field must be provided")
        return self

    def changes(self) -> dict[str, Any]:
        """Column -> new value for exactly the fields the client sent."""
        return dict(provided_fields(self))


class CareerGoalResponse(BaseModel):
    """A career goal as returned by the API (user_id is not repeated: it is the caller)."""

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "examples": [
                {
                    "id": "7c9e6679-7425-40de-944b-e07fc1f90ae7",
                    "target_role": "DevOps Engineer",
                    "seniority": "junior",
                    "description": "Prepare for a Junior DevOps Engineer position.",
                    "status": "active",
                    "created_at": "2026-11-12T10:00:00Z",
                    "updated_at": "2026-11-12T10:00:00Z",
                }
            ]
        },
    )

    id: UUID
    target_role: str
    seniority: Seniority
    description: str | None
    status: GoalStatus
    created_at: UtcDatetime
    updated_at: UtcDatetime
