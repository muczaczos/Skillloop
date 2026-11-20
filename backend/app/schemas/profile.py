"""API schemas of the profile (docs/API-DESIGN.md, section 5)."""

from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from app.models.profile import Language
from app.schemas.common import DisplayName, UtcDatetime, provided_fields, reject_null


class ProfileUpdate(BaseModel):
    """PATCH /profile. A field that is left out is not changed; an explicit null clears
    display_name and is an error for preferred_language. Unknown fields (id, user_id, ...)
    are rejected, never ignored."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [{"display_name": "Ann", "preferred_language": "pl"}]},
    )

    display_name: DisplayName | None = None
    preferred_language: Language | None = None

    _preferred_language_not_null = field_validator("preferred_language", mode="before")(reject_null)

    @model_validator(mode="after")
    def _at_least_one_field(self) -> "ProfileUpdate":
        if not self.model_fields_set:
            raise ValueError("at least one field must be provided")
        return self

    def changes(self) -> dict[str, Any]:
        """Column -> new value for exactly the fields the client sent."""
        return dict(provided_fields(self))


class ProfileResponse(BaseModel):
    """A profile as returned by the API (user_id is not repeated: it is the caller)."""

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "examples": [
                {
                    "id": "11111111-1111-1111-1111-111111111111",
                    "display_name": "Ann",
                    "preferred_language": "pl",
                    "created_at": "2026-11-12T10:00:00Z",
                    "updated_at": "2026-11-12T10:00:00Z",
                }
            ]
        },
    )

    id: UUID
    display_name: str | None
    preferred_language: Language
    created_at: UtcDatetime
    updated_at: UtcDatetime
