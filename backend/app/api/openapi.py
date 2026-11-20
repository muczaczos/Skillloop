"""Error responses for the OpenAPI document.

Builds the `responses=` dictionaries of the routers and endpoints. Codes and messages come from
the same constants the error handlers use (app/core/errors.py, app/api/errors.py), so the
examples in the documentation cannot drift away from what the API really sends.

Only documentation is produced here; nothing in this module changes what an endpoint does.
"""

from typing import Any

from app.api import errors as handlers
from app.core.errors import AppError
from app.schemas.errors import ErrorResponse, ValidationErrorResponse

Responses = dict[int | str, dict[str, Any]]

EXAMPLE_REQUEST_ID = "3f8e1a52-6d1b-4c0e-9a57-2b8f0c6d4e11"


def _example(code: str, message: str, summary: str, **extra: Any) -> dict[str, Any]:
    error = {"code": code, "message": message, **extra, "request_id": EXAMPLE_REQUEST_ID}
    return {"summary": summary, "value": {"error": error}}


def _response(
    description: str,
    examples: dict[str, dict[str, Any]],
    *,
    model: type = ErrorResponse,
    headers: dict[str, Any] | None = None,
) -> dict[str, Any]:
    response: dict[str, Any] = {
        "model": model,
        "description": description,
        "content": {"application/json": {"examples": examples}},
    }
    if headers:
        response["headers"] = headers
    return response


def _domain_examples(errors: tuple[type[AppError], ...]) -> dict[str, dict[str, Any]]:
    return {e.code: _example(e.code, e.default_message, e.code) for e in errors}


_UNAUTHORIZED_DESCRIPTION = (
    "Authentication failed: the access token is missing, invalid or expired."
)
_UNKNOWN_USER_NOTE = (
    " Also returned when the token is valid but its user no longer exists (detected when "
    "the endpoint writes a row that belongs to the user)."
)


def _unauthorized(description: str) -> Responses:
    return {
        401: _response(
            description,
            {
                "unauthorized": _example(
                    "UNAUTHORIZED", handlers.UNAUTHORIZED_MESSAGE, "Missing or invalid token"
                )
            },
            headers={
                "WWW-Authenticate": {
                    "description": "Always `Bearer`.",
                    "schema": {"type": "string", "examples": ["Bearer"]},
                }
            },
        )
    }


SERVICE_UNAVAILABLE: Responses = {
    503: _response(
        "A service the request depends on is temporarily unavailable (the database, or the "
        "keys used to verify access tokens). Try again later.",
        {
            "service_unavailable": _example(
                "SERVICE_UNAVAILABLE", handlers.SERVICE_UNAVAILABLE_MESSAGE, "Service unavailable"
            )
        },
    )
}

DATABASE_TIMEOUT: Responses = {
    504: _response(
        "The database did not answer within its statement timeout. Try again later.",
        {
            "database_timeout": _example(
                "DATABASE_TIMEOUT", handlers.DATABASE_TIMEOUT_MESSAGE, "Database timeout"
            )
        },
    )
}

VALIDATION_ERROR: Responses = {
    422: _response(
        "The request is invalid. `details` lists the problems; submitted values are never "
        "repeated. Replaces FastAPI's default HTTPValidationError.",
        {
            "validation_error": _example(
                "VALIDATION_ERROR",
                handlers.VALIDATION_ERROR_MESSAGE,
                "Invalid request",
                details=[{"field": "target_role", "message": "This field is required."}],
            )
        },
        model=ValidationErrorResponse,
    )
}


def not_found(*errors: type[AppError]) -> Responses:
    """404 with the domain errors the endpoint can raise."""
    return {404: _response("The resource was not found.", _domain_examples(errors))}


def conflict(*errors: type[AppError]) -> Responses:
    """409 with the domain errors the endpoint can raise."""
    return {
        409: _response("The request conflicts with the current state.", _domain_examples(errors))
    }


def protected_responses(*, user_may_be_missing: bool = False) -> Responses:
    """Responses that every endpoint behind authentication and the database can return.

    `user_may_be_missing`: the endpoint writes rows that reference auth.users, so a valid token
    of a deleted user is answered with 401 (see app/api/errors.py, USER_FK_CONSTRAINT).
    """
    description = _UNAUTHORIZED_DESCRIPTION + (_UNKNOWN_USER_NOTE if user_may_be_missing else "")
    return {**_unauthorized(description), **SERVICE_UNAVAILABLE, **DATABASE_TIMEOUT}
