"""Unexpected exceptions on the real application, and the application logging.

No route is added to the application: failures are injected through dependency overrides and
monkeypatching, so no database or network is needed.
"""

import asyncio
import io
import logging
from collections.abc import Iterator
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from app import main
from app.api.deps import get_current_user, get_db_pool
from app.api.errors import INTERNAL_ERROR_MESSAGE
from app.api.middleware import UnhandledErrorMiddleware
from app.core.config import settings
from app.core.logging import configure_logging
from app.models.auth import AuthenticatedUser
from app.schemas.errors import ErrorResponse
from app.services import career_goal_service, profile_service

PROFILE = "/api/v1/profile"
SECRET = "SECRET-detail user@example.com"
CAUSE_SECRET = "SECRET-cause password=hunter2"
TOKEN = "SECRET-token"


def _exploding_service(*args: Any) -> None:
    try:
        raise KeyError(CAUSE_SECRET)
    except KeyError as exc:
        raise RuntimeError(SECRET) from exc


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    user = AuthenticatedUser(id=uuid4())
    main.app.dependency_overrides[get_current_user] = lambda: user
    main.app.dependency_overrides[get_db_pool] = lambda: object()
    monkeypatch.setattr(profile_service, "get_profile", _exploding_service)
    # raise_server_exceptions stays True: an exception that escaped would fail the test.
    yield TestClient(main.app)
    main.app.dependency_overrides.clear()


def _get(client: TestClient, origin: str | None = None) -> Any:
    headers = {"Authorization": f"Bearer {TOKEN}"}
    if origin:
        headers["Origin"] = origin
    return client.get(PROFILE, headers=headers)


def test_unexpected_exception_gives_the_unified_500(client: TestClient) -> None:
    response = _get(client)

    assert response.status_code == 500
    assert response.headers["content-type"] == "application/json"
    error = ErrorResponse.model_validate(response.json()).error
    assert error.code == "INTERNAL_ERROR"
    assert error.message == INTERNAL_ERROR_MESSAGE
    assert set(response.json()["error"]) == {"code", "message", "request_id"}


def test_allowed_origin_can_read_the_500(client: TestClient) -> None:
    origin = settings.cors_origins[0]

    response = _get(client, origin)

    assert response.status_code == 500
    assert response.headers["access-control-allow-origin"] == origin


def test_other_origin_gets_no_cors_header(client: TestClient) -> None:
    response = _get(client, "https://evil.example")

    assert "access-control-allow-origin" not in response.headers


def test_response_and_log_hold_no_exception_message_or_token(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG, logger="app"):
        response = _get(client)

    for text in (response.text, caplog.text):
        assert "SECRET" not in text
        assert "hunter2" not in text
        assert "example.com" not in text


def test_log_is_enough_to_find_the_failure(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.ERROR, logger="app"):
        response = _get(client)

    request_id = str(UUID(response.json()["error"]["request_id"]))
    [record] = [r for r in caplog.records if r.name == "app.api.middleware"]
    message = record.getMessage()
    # The route template, without the /api/v1 prefix.
    assert f"request_id={request_id} GET /profile\n" in message
    assert "builtins.RuntimeError" in message
    assert "caused by builtins.KeyError" in message
    assert "test_unhandled_errors.py" in message
    assert "in _exploding_service" in message
    assert record.exc_info is None


def test_log_holds_the_route_template_and_no_resource_id(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(career_goal_service, "archive_goal", _exploding_service)
    goal_id = str(uuid4())

    with caplog.at_level(logging.ERROR, logger="app"):
        response = client.post(f"/api/v1/career-goals/{goal_id}/archive")

    assert response.status_code == 500
    assert "POST /career-goals/{goal_id}/archive\n" in caplog.text
    assert goal_id not in caplog.text


def test_failure_after_the_response_started_is_not_re_raised(
    caplog: pytest.LogCaptureFixture,
) -> None:
    sent: list[str] = []

    async def failing_app(scope: Any, receive: Any, send: Any) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        raise RuntimeError(SECRET)

    async def send(message: Any) -> None:
        sent.append(message["type"])

    async def receive() -> Any:
        return {"type": "http.request"}

    scope = {"type": "http", "method": "GET"}
    with caplog.at_level(logging.ERROR, logger="app"):
        asyncio.run(UnhandledErrorMiddleware(failing_app)(scope, receive, send))

    assert sent == ["http.response.start"]  # no second response
    assert "unhandled exception" in caplog.text
    assert "SECRET" not in caplog.text


def test_cors_wraps_the_unhandled_error_middleware() -> None:
    # user_middleware is ordered from the outermost to the innermost.
    classes = [middleware.cls for middleware in main.app.user_middleware]

    assert classes == [CORSMiddleware, UnhandledErrorMiddleware]


def test_application_info_messages_are_written_once() -> None:
    configure_logging()
    configure_logging()
    [handler] = [h for h in logging.getLogger("app").handlers if h.name == "skillloop-app"]
    stream = io.StringIO()
    previous = handler.setStream(stream)
    try:
        logging.getLogger("app.some_module").info("hello from the application")
    finally:
        handler.setStream(previous)

    assert stream.getvalue().count("hello from the application") == 1
    assert "INFO app.some_module" in stream.getvalue()
