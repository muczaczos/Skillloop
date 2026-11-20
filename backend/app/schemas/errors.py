"""Error models of the API (docs/API-DESIGN.md, section 13; decision D11).

These models DESCRIBE the error responses in the OpenAPI document and are used by the contract
tests. The handlers in app/api/errors.py still build the bodies themselves; a test checks that
what they send validates against these models, so the two cannot drift apart unnoticed.
"""

from uuid import UUID

from pydantic import BaseModel, Field


class ErrorBody(BaseModel):
    """The content of the `error` object of every error response."""

    code: str = Field(
        description=(
            "Stable, machine-readable error code, e.g. GOAL_NOT_FOUND. Clients should branch on "
            "the code, not on the message."
        ),
        examples=["GOAL_NOT_FOUND"],
    )
    message: str = Field(
        description="Human-readable explanation. It never contains user data or internals.",
        examples=["Career goal not found."],
    )
    request_id: UUID = Field(
        description="Identifier of this error response; it also appears in the server log.",
        examples=["3f8e1a52-6d1b-4c0e-9a57-2b8f0c6d4e11"],
    )


class ErrorResponse(BaseModel):
    """Unified error response."""

    error: ErrorBody


class ValidationErrorItem(BaseModel):
    """One problem found in the request."""

    field: str = Field(
        description="The offending field (dotted path), or the location of the problem.",
        examples=["target_role"],
    )
    message: str = Field(
        description="What is wrong with the field. The submitted value is never repeated.",
        examples=["This field is required."],
    )


class ValidationErrorBody(ErrorBody):
    """The `error` object of a 422 response: the unified fields plus `details`."""

    details: list[ValidationErrorItem] = Field(description="The problems found, at most 20.")


class ValidationErrorResponse(BaseModel):
    """Request validation error (HTTP 422). Replaces FastAPI's default HTTPValidationError."""

    error: ValidationErrorBody
