"""Row model of public.profiles: what the database returns. It is not an API schema."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

# Mirrors the CHECK constraint profiles_preferred_language_check.
Language = Literal["en", "pl"]


class ProfileRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    display_name: str | None
    preferred_language: Language
    created_at: datetime
    updated_at: datetime
