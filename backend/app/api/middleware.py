"""Last line of defence: unexpected exceptions become the unified 500 response.

This is a middleware and not an exception handler for `Exception` on purpose: Starlette runs
such a handler in ServerErrorMiddleware, OUTSIDE the CORS middleware, so a browser could not
read the response. Add this middleware BEFORE CORSMiddleware, so that CORS wraps it.

Errors with their own handler (app/api/errors.py) are answered further inside and never get
here.

What is logged: the request id, the method, the route template, and for the exception and its
causes the class name and the code locations. Never the exception message, source lines,
local variables, the real path, headers or the body: any of them can carry user data or
secrets.
"""

import logging
import traceback
from uuid import uuid4

from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.api.errors import INTERNAL_ERROR_MESSAGE
from app.schemas.errors import ErrorBody, ErrorResponse

logger = logging.getLogger(__name__)

# How many exceptions of a cause / context chain are described.
_MAX_CHAIN_LENGTH = 5


def _describe(exc: BaseException) -> str:
    """Class names and "file:line in function" of an exception and its causes."""
    lines: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen and len(seen) < _MAX_CHAIN_LENGTH:
        seen.add(id(current))
        prefix = "caused by " if lines else ""
        lines.append(f"{prefix}{type(current).__module__}.{type(current).__qualname__}")
        # lookup_lines=False: the source text is never read.
        frames = traceback.StackSummary.extract(
            traceback.walk_tb(current.__traceback__), lookup_lines=False
        )
        lines.extend(f"  at {f.filename}:{f.lineno} in {f.name}" for f in frames)
        if current.__cause__ is not None:
            current = current.__cause__
        else:
            current = None if current.__suppress_context__ else current.__context__
    return "\n".join(lines)


class UnhandledErrorMiddleware:
    """Pure ASGI middleware. It does not re-raise: the server would log the full traceback,
    including the exception message."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        response_started = False

        async def tracking_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, receive, tracking_send)
        except Exception as exc:  # noqa: BLE001 - catching everything is the purpose here
            request_id = uuid4()
            # The router stores the matched route in the scope; its path is the template
            # (without the /api/v1 prefix), so no resource id reaches the log.
            route = getattr(scope.get("route"), "path", "<unmatched>")
            logger.error(
                "unhandled exception request_id=%s %s %s\n%s",
                request_id,
                scope["method"],
                route,
                _describe(exc),
            )
            if response_started:
                return  # too late for another response; the server drops the connection
            body = ErrorResponse(
                error=ErrorBody(
                    code="INTERNAL_ERROR", message=INTERNAL_ERROR_MESSAGE, request_id=request_id
                )
            )
            response = JSONResponse(status_code=500, content=body.model_dump(mode="json"))
            await response(scope, receive, send)
