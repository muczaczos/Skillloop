from uuid import UUID

from pydantic import BaseModel, ConfigDict


class AuthenticatedUser(BaseModel):
    """Identity of the caller, derived exclusively from a verified access token."""

    model_config = ConfigDict(frozen=True)

    id: UUID
