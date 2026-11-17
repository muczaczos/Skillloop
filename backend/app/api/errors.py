"""Central translation of exceptions into API error responses.

Response body (docs/API-DESIGN.md, section 13):

    {"error": {"code": "...", "message": "...", "request_id": "<uuid>"}}

`details` is only used for request validation errors and is not produced here.

What is handled:
- AppError (app/core/errors.py): business errors raised by services; status and code come
  from the exception.
- DatabaseUnavailableError: 503.
- psycopg.Error: mapped by SQLSTATE (see _map_database_error). Anything not listed there is
  a bug or an unexpected failure: 500, never a client error.

The unique violation of one_active_goal_per_user is mapped to ActiveGoalExistsError by
career_goal_service.create_goal(). It is deliberately NOT mapped again here: every 23505
that reaches this module is unexpected and answers 500.

Nothing from the exception (SQL, psycopg message, DETAIL with row values) is sent to the
client or logged: only the exception class, SQLSTATE and constraint name are.

request_id: no request-id middleware exists yet, so one is generated per error response.
It is in the body and in the log line, which is what links the two.
"""

import logging
from dataclasses import dataclass
from uuid import uuid4

import psycopg
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.errors import AppError
from app.db.errors import DatabaseUnavailableError

logger = logging.getLogger(__name__)

# Fixed client-facing messages. They never contain details of the failure.
INTERNAL_ERROR_MESSAGE = "An internal error occurred."
UNAUTHORIZED_MESSAGE = "Invalid or missing authentication token."
CONFLICT_MESSAGE = "The request conflicts with the current state of the resource."
VALIDATION_ERROR_MESSAGE = "The submitted data is invalid."
SERVICE_UNAVAILABLE_MESSAGE = "The service is temporarily unavailable. Please try again later."
DATABASE_TIMEOUT_MESSAGE = "The database did not answer in time. Please try again later."

# career_goals.user_id -> auth.users(id): the token is valid but its user no longer exists.
USER_FK_CONSTRAINT = "career_goals_user_id_fkey"

# 22xxx data exceptions that user input can legitimately cause. Any other 22xxx code is
# unexpected (a bug in our query or code) and answers 500.
# Note: a NUL character is NOT reported as 22021. psycopg refuses it on the client side as a
# DataError WITHOUT a SQLSTATE, which ends up as 500 below. That is unreachable in practice:
# the request schemas reject NUL before any SQL runs.
_CLIENT_DATA_SQLSTATES = frozenset(
    {
        "22001",  # string_data_right_truncation
        "22003",  # numeric_value_out_of_range
        "22021",  # character_not_in_repertoire (invalid byte sequence for the encoding)
    }
)


@dataclass(frozen=True)
class ErrorSpec:
    status_code: int
    code: str
    message: str
    headers: dict[str, str] | None = None


def _map_database_error(sqlstate: str | None, constraint: str | None) -> ErrorSpec:
    if sqlstate == "23503":
        if constraint == USER_FK_CONSTRAINT:
            return ErrorSpec(
                401, "UNAUTHORIZED", UNAUTHORIZED_MESSAGE, {"WWW-Authenticate": "Bearer"}
            )
        return ErrorSpec(409, "CONFLICT", CONFLICT_MESSAGE)
    if sqlstate in _CLIENT_DATA_SQLSTATES:
        return ErrorSpec(422, "VALIDATION_ERROR", VALIDATION_ERROR_MESSAGE)
    if sqlstate == "57014":
        return ErrorSpec(504, "DATABASE_TIMEOUT", DATABASE_TIMEOUT_MESSAGE)
    # 23505, 23514, 23502, 42501, other 22xxx, and every other psycopg error: our bug or an
    # unexpected failure, not something the client can fix.
    return ErrorSpec(500, "INTERNAL_ERROR", INTERNAL_ERROR_MESSAGE)


def _body(spec: ErrorSpec, request_id: str) -> dict[str, dict[str, str]]:
    return {"error": {"code": spec.code, "message": spec.message, "request_id": request_id}}


def _respond(spec: ErrorSpec, request_id: str) -> JSONResponse:
    return JSONResponse(
        status_code=spec.status_code, content=_body(spec, request_id), headers=spec.headers
    )


async def app_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AppError)
    request_id = str(uuid4())
    level = logging.ERROR if exc.status_code >= 500 else logging.INFO
    logger.log(level, "%s (%s) request_id=%s", exc.code, type(exc).__name__, request_id)
    return _respond(ErrorSpec(exc.status_code, exc.code, exc.message), request_id)


async def database_unavailable_handler(request: Request, exc: Exception) -> JSONResponse:
    request_id = str(uuid4())
    # str(exc) holds only the cause's class name / SQLSTATE: safe to log.
    logger.error("database unavailable: %s request_id=%s", exc, request_id)
    spec = ErrorSpec(503, "SERVICE_UNAVAILABLE", SERVICE_UNAVAILABLE_MESSAGE)
    return _respond(spec, request_id)


async def database_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, psycopg.Error)
    request_id = str(uuid4())
    sqlstate = exc.sqlstate
    constraint = exc.diag.constraint_name
    spec = _map_database_error(sqlstate, constraint)

    # Never log str(exc) or the traceback: psycopg messages can contain row values.
    level = logging.ERROR if spec.status_code >= 500 else logging.WARNING
    logger.log(
        level,
        "database error %s: SQLSTATE=%s constraint=%s -> %s request_id=%s",
        type(exc).__name__,
        sqlstate,
        constraint,
        spec.code,
        request_id,
    )
    return _respond(spec, request_id)


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(DatabaseUnavailableError, database_unavailable_handler)
    app.add_exception_handler(psycopg.Error, database_error_handler)
