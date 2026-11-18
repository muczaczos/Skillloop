"""Central translation of exceptions into API error responses.

Response body (docs/API-DESIGN.md, section 13):

    {"error": {"code": "...", "message": "...", "request_id": "<uuid>"}}

`details` is only used for request validation errors (422): a list of
{"field": ..., "message": ...} and nothing else (see _validation_details).

What is handled:
- AppError (app/core/errors.py): business errors raised by services; status and code come
  from the exception.
- DatabaseUnavailableError: 503.
- psycopg.Error: mapped by SQLSTATE (see _map_database_error). Anything not listed there is
  a bug or an unexpected failure: 500, never a client error.
- RequestValidationError: 422 VALIDATION_ERROR. The messages come from a fixed table by error
  type, not from Pydantic's `msg`: some of those texts repeat part of the submitted value
  (e.g. an invalid UUID: "found `N` at 2"). `input`, `ctx` and `url` are never read.
- HTTPException (FastAPI's and Starlette's: the authentication dependency, unknown paths,
  wrong methods): the status is kept; the code and the message come from a fixed table by
  status, never from the exception's `detail`, which could hold anything. Only a short list
  of headers is passed on (see _FORWARDED_HEADERS). For a 405 the `Allow` header is rebuilt
  from the real routing (see _allowed_methods), because Starlette names only the methods of
  the first route that matches the path.

The unique violation of one_active_goal_per_user is mapped to ActiveGoalExistsError by
career_goal_service.create_goal(). It is deliberately NOT mapped again here: every 23505
that reaches this module is unexpected and answers 500.

Nothing from the exception (SQL, psycopg message, DETAIL with row values) is sent to the
client or logged: only the exception class, SQLSTATE and constraint name are.

request_id: no request-id middleware exists yet, so one is generated per error response.
It is in the body and in the log line, which is what links the two.
"""

import logging
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any
from uuid import uuid4

import psycopg
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.routing import Match

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
FORBIDDEN_MESSAGE = "You do not have permission to perform this action."
NOT_FOUND_MESSAGE = "The requested resource was not found."
METHOD_NOT_ALLOWED_MESSAGE = "This method is not allowed for the requested resource."
CLIENT_ERROR_MESSAGE = "The request could not be processed."

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


# --- request validation (422) -------------------------------------------------------------

# Where a field of the request lives; FastAPI puts it first in an error location.
_LOCATIONS = ("body", "query", "path", "header", "cookie")
# Upper bounds, so that a hostile request cannot make the answer huge.
_MAX_VALIDATION_DETAILS = 20
_MAX_FIELD_LENGTH = 100
_MAX_MESSAGE_LENGTH = 200
_VALUE_ERROR_PREFIX = "Value error, "
GENERIC_FIELD_MESSAGE = "The value is invalid."

# Message by Pydantic error type. Texts are fixed; none of them contains the value.
_VALIDATION_MESSAGES: dict[str, str] = {
    "missing": "This field is required.",
    "extra_forbidden": "This field is not allowed.",
    "string_too_short": "The value is too short.",
    "string_too_long": "The value is too long.",
    "too_short": "The value is too short.",  # what Annotated[str, Field(min_length=...)] reports
    "too_long": "The value is too long.",
    "string_type": "The value must be text.",
    "int_parsing": "The value must be an integer.",
    "int_type": "The value must be an integer.",
    "int_from_float": "The value must be an integer.",
    "float_parsing": "The value must be a number.",
    "float_type": "The value must be a number.",
    "bool_parsing": "The value must be true or false.",
    "bool_type": "The value must be true or false.",
    "uuid_parsing": "The value must be a valid UUID.",
    "uuid_type": "The value must be a valid UUID.",
    "literal_error": "The value is not one of the allowed options.",
    "enum": "The value is not one of the allowed options.",
    "greater_than": "The value is out of the allowed range.",
    "greater_than_equal": "The value is out of the allowed range.",
    "less_than": "The value is out of the allowed range.",
    "less_than_equal": "The value is out of the allowed range.",
    "json_invalid": "The request body is not valid JSON.",
    "model_type": "The value has the wrong structure.",
    "model_attributes_type": "The value has the wrong structure.",  # e.g. a body that is not an object
    "dict_type": "The value has the wrong structure.",
    "list_type": "The value has the wrong structure.",
}


def _validation_detail(error: Mapping[str, Any]) -> dict[str, str]:
    """One {"field", "message"} entry. Reads only `loc`, `type` and, for value errors raised
    by our own validators, `msg`. Never `input`, `ctx` or `url`."""
    parts = [str(part) for part in error.get("loc", ())]
    location = parts[0] if parts and parts[0] in _LOCATIONS else None
    path = parts[1:] if location else parts
    error_type = str(error.get("type", ""))
    if error_type == "json_invalid":
        path = []  # the location then holds a character offset of the body, not a field
    # Errors that belong to no single field (a whole body) are named after their location.
    field = (".".join(path) or location or "body")[:_MAX_FIELD_LENGTH]

    if error_type == "value_error":
        # Raised by validators of this code base (e.g. "must not be null"), whose messages are
        # written not to contain the value. Pydantic prefixes them with "Value error, ".
        message = str(error.get("msg", "")).removeprefix(_VALUE_ERROR_PREFIX)
        message = message[:_MAX_MESSAGE_LENGTH] or GENERIC_FIELD_MESSAGE
    else:
        message = _VALIDATION_MESSAGES.get(error_type, GENERIC_FIELD_MESSAGE)
    return {"field": field, "message": message}


def _validation_details(exc: RequestValidationError) -> list[dict[str, str]]:
    return [_validation_detail(error) for error in exc.errors()[:_MAX_VALIDATION_DETAILS]]


# HTTPException statuses with their own code and message. Every other status below 500 is a
# CLIENT_ERROR and every other one an INTERNAL_ERROR, each with a generic message.
_HTTP_STATUS_ERRORS: dict[int, tuple[str, str]] = {
    401: ("UNAUTHORIZED", UNAUTHORIZED_MESSAGE),
    403: ("FORBIDDEN", FORBIDDEN_MESSAGE),
    404: ("NOT_FOUND", NOT_FOUND_MESSAGE),
    405: ("METHOD_NOT_ALLOWED", METHOD_NOT_ALLOWED_MESSAGE),
    503: ("SERVICE_UNAVAILABLE", SERVICE_UNAVAILABLE_MESSAGE),
}

# Headers of an HTTPException that are passed on to the client (lower case). Anything else
# (cookies, internal headers) is dropped.
_FORWARDED_HEADERS = frozenset({"www-authenticate", "allow", "retry-after"})


# The methods probed to build the Allow header of a 405. A method is listed only if a route really
# serves it: API routes do not serve HEAD, but the Starlette documentation routes (/docs, /redoc,
# /openapi.json) do. OPTIONS is not routed by this application, so it is never listed. Sorted on
# purpose: the header is the same whatever order the routes were declared in.
_PROBED_METHODS = ("DELETE", "GET", "HEAD", "PATCH", "POST", "PUT")


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


def _map_http_exception(exc: StarletteHTTPException) -> ErrorSpec:
    headers = {
        name: value
        for name, value in (exc.headers or {}).items()
        if name.lower() in _FORWARDED_HEADERS
    }
    known = _HTTP_STATUS_ERRORS.get(exc.status_code)
    if known is not None:
        code, message = known
    elif exc.status_code >= 500:
        code, message = "INTERNAL_ERROR", INTERNAL_ERROR_MESSAGE
    else:
        code, message = "CLIENT_ERROR", CLIENT_ERROR_MESSAGE
    return ErrorSpec(exc.status_code, code, message, headers or None)


def _allowed_methods(request: Request) -> list[str]:
    """The methods that the application really routes for the path of `request`, sorted.

    Starlette answers 405 from the FIRST route that matches the path, and that route lists
    only its own methods: with GET and PATCH on one path the header says "GET" or "PATCH"
    depending on the declaration order (RFC 9110, 15.5.6 asks for all of them). So the
    routing is asked instead, with the public Route.matches() contract only: a method is
    allowed if some route matches the path AND that method completely.

    Each probe uses a fresh, minimal scope; the scope of the real request is never touched.
    """
    allowed = []
    for method in _PROBED_METHODS:
        probe = {
            "type": "http",
            "method": method,
            "path": request.scope["path"],
            "root_path": request.scope.get("root_path", ""),
            "headers": [],
            "query_string": b"",
            "app": request.app,
        }
        if any(route.matches(probe)[0] is Match.FULL for route in request.app.router.routes):
            allowed.append(method)
    return allowed


def _with_complete_allow_header(spec: ErrorSpec, request: Request) -> ErrorSpec:
    """Replace the Allow header of a 405 by the full list. If the routing knows no method for
    this path, the header of the exception is left as it is (nothing is invented)."""
    allowed = _allowed_methods(request)
    if not allowed:
        return spec
    headers = {k: v for k, v in (spec.headers or {}).items() if k.lower() != "allow"}
    headers["Allow"] = ", ".join(allowed)
    return replace(spec, headers=headers)


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


async def validation_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    request_id = str(uuid4())
    details = _validation_details(exc)
    # Only the number of problems is logged: field names can be chosen by the client.
    logger.info("VALIDATION_ERROR: %d problem(s) request_id=%s", len(exc.errors()), request_id)
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "VALIDATION_ERROR",
                "message": VALIDATION_ERROR_MESSAGE,
                "details": details,
                "request_id": request_id,
            }
        },
    )


async def http_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    request_id = str(uuid4())
    spec = _map_http_exception(exc)
    if exc.status_code == 405:
        spec = _with_complete_allow_header(spec, request)
    # Neither `detail` nor the request path is logged: both can carry data.
    level = logging.ERROR if spec.status_code >= 500 else logging.INFO
    logger.log(level, "HTTP %s %s request_id=%s", spec.status_code, spec.code, request_id)
    return _respond(spec, request_id)


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(DatabaseUnavailableError, database_unavailable_handler)
    app.add_exception_handler(psycopg.Error, database_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
