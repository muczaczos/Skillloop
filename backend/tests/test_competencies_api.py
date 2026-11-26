"""HTTP tests of the competency endpoints (list, add, update, archive), no database.

Requests go through the real application (`app.main.app`): routing, authentication dependency,
request validation, response model and the central error handlers. Only the service layer is
replaced, so what is under test is the HTTP behaviour of the endpoints.
"""

import inspect
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from psycopg import errors as pg_errors

import app.api.competencies as competencies_api
from app import main
from app.api.deps import get_current_user, get_db_pool
from app.core.errors import (
    CompetencyArchivedError,
    CompetencyLimitReachedError,
    CompetencyNameExistsError,
    CompetencyNotFoundError,
    GoalArchivedError,
    GoalNotFoundError,
)
from app.db.errors import DatabaseUnavailableError
from app.models.auth import AuthenticatedUser
from app.models.competency import CompetencyRecord
from app.schemas.competency import CompetencyCreate, CompetencyUpdate

USER = AuthenticatedUser(id=UUID("11111111-1111-1111-1111-111111111111"))
GOAL_ID = UUID("22222222-2222-2222-2222-222222222222")
COMPETENCY_ID = UUID("33333333-3333-3333-3333-333333333333")
POOL = object()  # stands in for the connection pool; the fake service never uses it
T0 = datetime(2026, 11, 24, 9, 30, tzinfo=UTC)
URL = f"/api/v1/career-goals/{GOAL_ID}/competencies"
TEMPLATE = "/api/v1/career-goals/{goal_id}/competencies"
VALID = {
    "name": "Docker",
    "knowledge_type": "application",
    "importance": "critical",
    "expected_level": "intermediate",
}
RESPONSE_FIELDS = {
    "id",
    "career_goal_id",
    "name",
    "description",
    "rationale",
    "category",
    "knowledge_type",
    "importance",
    "expected_level",
    "display_order",
    "status",
    "created_at",
    "updated_at",
}


def record(**overrides: Any) -> CompetencyRecord:
    values: dict[str, Any] = {
        "id": COMPETENCY_ID,
        "user_id": USER.id,
        "career_goal_id": GOAL_ID,
        "name": "Docker",
        "description": None,
        "rationale": None,
        "category": "Infrastructure",
        "knowledge_type": "application",
        "importance": "critical",
        "expected_level": "intermediate",
        "display_order": 0,
        "status": "active",
        "created_at": T0,
        "updated_at": T0,
    }
    values.update(overrides)
    return CompetencyRecord(**values)


class FakeService:
    """Records the calls of the endpoints and answers (or raises) what the test sets."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Any, Any, UUID, Any]] = []
        self.listed: list[CompetencyRecord] | BaseException = [record()]
        self.created: CompetencyRecord | BaseException = record()
        self.changed: CompetencyRecord | BaseException = record()

    def update_competency(
        self, pool: Any, user: Any, competency_id: UUID, data: CompetencyUpdate
    ) -> CompetencyRecord:
        return self._change("update", pool, user, competency_id, data)

    def archive_competency(self, pool: Any, user: Any, competency_id: UUID) -> CompetencyRecord:
        return self._change("archive", pool, user, competency_id, None)

    def _change(self, *call: Any) -> CompetencyRecord:
        self.calls.append(call)
        if isinstance(self.changed, BaseException):
            raise self.changed
        return self.changed

    def list_competencies(self, pool: Any, user: Any, goal_id: UUID) -> list[CompetencyRecord]:
        self.calls.append(("list", pool, user, goal_id, None))
        if isinstance(self.listed, BaseException):
            raise self.listed
        return self.listed

    def create_competency(
        self, pool: Any, user: Any, goal_id: UUID, data: CompetencyCreate
    ) -> CompetencyRecord:
        self.calls.append(("create", pool, user, goal_id, data))
        if isinstance(self.created, BaseException):
            raise self.created
        return self.created


@pytest.fixture
def service(monkeypatch: pytest.MonkeyPatch) -> FakeService:
    fake = FakeService()
    monkeypatch.setattr(
        competencies_api.competency_service, "list_competencies", fake.list_competencies
    )
    monkeypatch.setattr(
        competencies_api.competency_service, "create_competency", fake.create_competency
    )
    for name in ("update_competency", "archive_competency"):
        monkeypatch.setattr(competencies_api.competency_service, name, getattr(fake, name))
    return fake


@pytest.fixture
def client(service: FakeService) -> Iterator[TestClient]:
    """Authentication and the pool are replaced; everything else is the real application."""
    main.app.dependency_overrides[get_current_user] = lambda: USER
    main.app.dependency_overrides[get_db_pool] = lambda: POOL
    try:
        yield TestClient(main.app, raise_server_exceptions=False)
    finally:
        main.app.dependency_overrides.pop(get_current_user, None)
        main.app.dependency_overrides.pop(get_db_pool, None)


def assert_error(response: Any, status: int, code: str) -> dict[str, Any]:
    assert response.status_code == status
    body = response.json()
    assert set(body) == {"error"}
    error = body["error"]
    assert error["code"] == code
    UUID(error["request_id"])
    return error


# --- routes ------------------------------------------------------------------------------


def test_the_handlers_are_plain_functions_so_they_run_in_the_thread_pool() -> None:
    assert not inspect.iscoroutinefunction(competencies_api.list_competencies)
    assert not inspect.iscoroutinefunction(competencies_api.create_competency)


def test_the_resource_offers_exactly_get_and_post() -> None:
    assert sorted(main.app.openapi()["paths"][TEMPLATE]) == ["get", "post"]


@pytest.mark.parametrize("method", ["put", "patch", "delete"])
def test_other_methods_answer_405_with_the_exact_allow_header(
    client: TestClient, method: str
) -> None:
    response = getattr(client, method)(URL)

    assert_error(response, 405, "METHOD_NOT_ALLOWED")
    assert response.headers["allow"] == "GET, POST"


def test_the_goal_routes_are_not_disturbed(client: TestClient) -> None:
    response = client.post(f"/api/v1/career-goals/{GOAL_ID}")

    assert_error(response, 405, "METHOD_NOT_ALLOWED")
    assert response.headers["allow"] == "PATCH"


# --- GET -------------------------------------------------------------------------------------


def test_get_returns_200_and_the_list(client: TestClient, service: FakeService) -> None:
    service.listed = [record(), record(id=UUID(int=4), name="Git", display_order=1)]

    response = client.get(URL)

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"competencies"}
    assert [c["name"] for c in body["competencies"]] == ["Docker", "Git"]  # repository order
    assert body["competencies"][0] == {
        "id": str(COMPETENCY_ID),
        "career_goal_id": str(GOAL_ID),
        "name": "Docker",
        "description": None,
        "rationale": None,
        "category": "Infrastructure",
        "knowledge_type": "application",
        "importance": "critical",
        "expected_level": "intermediate",
        "display_order": 0,
        "status": "active",
        "created_at": "2026-11-24T09:30:00Z",
        "updated_at": "2026-11-24T09:30:00Z",
    }


def test_get_calls_the_service_with_the_user_from_the_token_and_the_goal_id(
    client: TestClient, service: FakeService
) -> None:
    client.get(URL)

    assert service.calls == [("list", POOL, USER, GOAL_ID, None)]


def test_get_returns_an_empty_list(client: TestClient, service: FakeService) -> None:
    service.listed = []

    response = client.get(URL)

    assert response.status_code == 200
    assert response.json() == {"competencies": []}


def test_get_returns_active_and_archived_competencies(
    client: TestClient, service: FakeService
) -> None:
    service.listed = [record(), record(id=UUID(int=4), name="Old", status="archived")]

    response = client.get(URL)

    assert [c["status"] for c in response.json()["competencies"]] == ["active", "archived"]


def test_get_for_an_archived_goal_returns_what_the_service_returns(
    client: TestClient, service: FakeService
) -> None:
    # The goal rule is the service's: for an archived goal it returns the frozen competencies,
    # and the router must add no refusal of its own.
    service.listed = [record(status="archived")]

    response = client.get(URL)

    assert response.status_code == 200
    assert response.json()["competencies"][0]["status"] == "archived"


def test_get_never_returns_user_id(client: TestClient) -> None:
    response = client.get(URL)

    for item in response.json()["competencies"]:
        assert set(item) == RESPONSE_FIELDS
    assert "user_id" not in response.text


def test_get_without_a_token_is_401() -> None:
    response = TestClient(main.app, raise_server_exceptions=False).get(URL)

    assert_error(response, 401, "UNAUTHORIZED")
    assert response.headers["www-authenticate"] == "Bearer"


def test_get_with_an_invalid_token_is_401() -> None:
    response = TestClient(main.app, raise_server_exceptions=False).get(
        URL, headers={"Authorization": "Bearer not-a-token"}
    )

    assert_error(response, 401, "UNAUTHORIZED")


def test_get_for_a_missing_or_foreign_goal_is_404(client: TestClient, service: FakeService) -> None:
    service.listed = GoalNotFoundError()

    assert_error(client.get(URL), 404, "GOAL_NOT_FOUND")


def test_get_with_a_malformed_goal_id_is_422_and_does_not_reach_the_service(
    client: TestClient, service: FakeService
) -> None:
    response = client.get("/api/v1/career-goals/not-a-uuid/competencies")

    error = assert_error(response, 422, "VALIDATION_ERROR")
    assert "goal_id" in {d["field"] for d in error["details"]}
    assert service.calls == []


# --- POST ------------------------------------------------------------------------------------


def test_post_creates_the_competency_and_returns_201(client: TestClient) -> None:
    response = client.post(URL, json=VALID)

    assert response.status_code == 201
    body = response.json()
    assert set(body) == RESPONSE_FIELDS
    assert body["id"] == str(COMPETENCY_ID)
    assert body["status"] == "active"
    assert "user_id" not in response.text


def test_post_calls_the_service_with_the_user_the_goal_id_and_the_validated_data(
    client: TestClient, service: FakeService
) -> None:
    client.post(URL, json=VALID)

    [(name, pool, user, goal_id, data)] = service.calls
    assert (name, pool, user, goal_id) == ("create", POOL, USER, GOAL_ID)
    assert data == CompetencyCreate(**VALID)


def test_post_applies_the_defaults_of_the_optional_fields(
    client: TestClient, service: FakeService
) -> None:
    client.post(URL, json=VALID)

    data = service.calls[0][4]
    assert (data.description, data.rationale, data.category, data.display_order) == (
        None,
        None,
        None,
        0,
    )


def test_post_passes_the_optional_fields_on(client: TestClient, service: FakeService) -> None:
    body = {
        **VALID,
        "description": "Containers.",
        "rationale": "Used everywhere.",
        "category": "IaC",
        "display_order": 5,
    }

    client.post(URL, json=body)

    assert service.calls[0][4] == CompetencyCreate(**body)


def test_post_normalises_the_input_before_the_service_sees_it(
    client: TestClient, service: FakeService
) -> None:
    client.post(URL, json={**VALID, "name": "  Docker  ", "description": "   "})

    data = service.calls[0][4]
    assert (data.name, data.description) == ("Docker", None)


@pytest.mark.parametrize("missing", ["name", "knowledge_type", "importance", "expected_level"])
def test_post_requires_the_required_fields(
    client: TestClient, service: FakeService, missing: str
) -> None:
    body = {k: v for k, v in VALID.items() if k != missing}

    error = assert_error(client.post(URL, json=body), 422, "VALIDATION_ERROR")

    assert missing in {d["field"] for d in error["details"]}
    assert service.calls == []


@pytest.mark.parametrize(
    "change",
    [
        {"name": ""},
        {"name": "   "},
        {"name": 5},
        {"name": None},
        {"knowledge_type": "magic"},
        {"importance": "urgent"},
        {"expected_level": "godlike"},
        {"display_order": "first"},
        {"display_order": -1},
        {"description": 5},
        {"category": ["a"]},
    ],
)
def test_post_rejects_wrong_types_and_values(
    client: TestClient, service: FakeService, change: dict[str, Any]
) -> None:
    assert_error(client.post(URL, json={**VALID, **change}), 422, "VALIDATION_ERROR")
    assert service.calls == []


@pytest.mark.parametrize(
    "field",
    ["user_id", "id", "career_goal_id", "status", "created_at", "updated_at", "unknown"],
)
def test_post_rejects_unknown_fields(client: TestClient, service: FakeService, field: str) -> None:
    error = assert_error(client.post(URL, json={**VALID, field: "x"}), 422, "VALIDATION_ERROR")

    assert field in {d["field"] for d in error["details"]}
    assert service.calls == []


def test_post_with_a_malformed_goal_id_is_422(client: TestClient, service: FakeService) -> None:
    response = client.post("/api/v1/career-goals/not-a-uuid/competencies", json=VALID)

    assert_error(response, 422, "VALIDATION_ERROR")
    assert service.calls == []


def test_post_without_a_body_is_422(client: TestClient, service: FakeService) -> None:
    assert_error(client.post(URL), 422, "VALIDATION_ERROR")
    assert service.calls == []


def test_post_does_not_echo_the_submitted_value_in_a_validation_error(
    client: TestClient,
) -> None:
    response = client.post(URL, json={**VALID, "importance": "CANARY-7b3d9"})

    assert "CANARY-7b3d9" not in response.text


@pytest.mark.parametrize(
    ("exc", "status", "code"),
    [
        (GoalNotFoundError(), 404, "GOAL_NOT_FOUND"),
        (GoalArchivedError(), 409, "GOAL_ARCHIVED"),
        (CompetencyLimitReachedError(), 409, "COMPETENCY_LIMIT_REACHED"),
        (CompetencyNameExistsError(), 409, "COMPETENCY_NAME_EXISTS"),
    ],
)
def test_post_turns_the_errors_of_the_service_into_the_unified_response(
    client: TestClient, service: FakeService, exc: BaseException, status: int, code: str
) -> None:
    service.created = exc

    assert_error(client.post(URL, json=VALID), status, code)


def test_post_without_a_token_is_401(service: FakeService) -> None:
    response = TestClient(main.app, raise_server_exceptions=False).post(URL, json=VALID)

    assert_error(response, 401, "UNAUTHORIZED")
    assert service.calls == []


def test_post_with_an_invalid_token_is_401(service: FakeService) -> None:
    response = TestClient(main.app, raise_server_exceptions=False).post(
        URL, json=VALID, headers={"Authorization": "Bearer not-a-token"}
    )

    assert_error(response, 401, "UNAUTHORIZED")
    assert service.calls == []


# --- infrastructure errors (both endpoints) ------------------------------------------------------


@pytest.mark.parametrize("method", ["get", "post"])
def test_an_unavailable_database_is_503(
    client: TestClient, service: FakeService, method: str
) -> None:
    service.listed = service.created = DatabaseUnavailableError("database unavailable")

    response = client.request(method.upper(), URL, json=VALID if method == "post" else None)

    assert_error(response, 503, "SERVICE_UNAVAILABLE")


@pytest.mark.parametrize("method", ["get", "post"])
def test_a_database_timeout_is_504(client: TestClient, service: FakeService, method: str) -> None:
    service.listed = service.created = pg_errors.QueryCanceled("statement timeout")

    response = client.request(method.upper(), URL, json=VALID if method == "post" else None)

    assert_error(response, 504, "DATABASE_TIMEOUT")


# --- PATCH /competencies/{id} and POST /competencies/{id}/archive ------------------------------

ITEM_URL = f"/api/v1/competencies/{COMPETENCY_ID}"
ARCHIVE_URL = f"{ITEM_URL}/archive"


def patch_item(client: TestClient) -> Any:
    return client.patch(ITEM_URL, json={"name": "Git"})


def archive_item(client: TestClient) -> Any:
    return client.post(ARCHIVE_URL)


ITEM_CALLS = pytest.mark.parametrize("call", [patch_item, archive_item], ids=["patch", "archive"])


def test_the_item_handlers_are_plain_functions() -> None:
    assert not inspect.iscoroutinefunction(competencies_api.update_competency)
    assert not inspect.iscoroutinefunction(competencies_api.archive_competency)


@pytest.mark.parametrize(
    ("method", "url", "allow"),
    [
        ("get", ITEM_URL, "PATCH"),
        ("delete", ITEM_URL, "PATCH"),
        ("put", ITEM_URL, "PATCH"),
        ("get", ARCHIVE_URL, "POST"),
        ("patch", ARCHIVE_URL, "POST"),
    ],
)
def test_other_methods_of_the_item_paths_answer_405(
    client: TestClient, method: str, url: str, allow: str
) -> None:
    response = getattr(client, method)(url)

    assert_error(response, 405, "METHOD_NOT_ALLOWED")
    assert response.headers["allow"] == allow


def test_patch_returns_the_updated_competency_without_the_owner(
    client: TestClient, service: FakeService
) -> None:
    service.changed = record(name="Git")

    response = patch_item(client)

    assert response.status_code == 200
    assert set(response.json()) == RESPONSE_FIELDS
    assert response.json()["name"] == "Git"
    assert str(USER.id) not in response.text


def test_patch_passes_the_user_the_id_and_only_the_sent_fields(
    client: TestClient, service: FakeService
) -> None:
    client.patch(ITEM_URL, json={"importance": "low", "description": None})

    ((name, pool, user, competency_id, data),) = service.calls
    assert (name, pool, user, competency_id) == ("update", POOL, USER, COMPETENCY_ID)
    assert data.changes() == {"importance": "low", "description": None}


def test_archive_returns_the_archived_competency(client: TestClient, service: FakeService) -> None:
    service.changed = record(status="archived")

    response = archive_item(client)

    assert response.status_code == 200
    assert set(response.json()) == RESPONSE_FIELDS
    assert response.json()["status"] == "archived"
    assert service.calls == [("archive", POOL, USER, COMPETENCY_ID, None)]


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"name": None},
        {"name": " "},
        {"display_order": None},
        {"display_order": "1"},
        {"status": "archived"},
        {"career_goal_id": str(GOAL_ID)},
        {"name": "Git", "user_id": str(USER.id)},
        {"importance": "urgent"},
        [],
        None,
    ],
)
def test_an_invalid_patch_is_422_and_does_not_reach_the_service(
    client: TestClient, service: FakeService, body: Any
) -> None:
    response = client.patch(ITEM_URL, json=body)

    error = assert_error(response, 422, "VALIDATION_ERROR")
    assert error["details"]
    assert service.calls == []


@pytest.mark.parametrize(
    ("method", "url", "body"),
    [
        ("patch", "/api/v1/competencies/not-a-uuid", {"name": "Git"}),
        ("post", "/api/v1/competencies/not-a-uuid/archive", None),
    ],
)
def test_a_malformed_competency_id_is_422(
    client: TestClient, service: FakeService, method: str, url: str, body: Any
) -> None:
    response = client.request(method.upper(), url, json=body)

    error = assert_error(response, 422, "VALIDATION_ERROR")
    assert "competency_id" in {d["field"] for d in error["details"]}
    assert service.calls == []


@ITEM_CALLS
@pytest.mark.parametrize(
    ("raised", "status", "code"),
    [
        (CompetencyNotFoundError(), 404, "COMPETENCY_NOT_FOUND"),
        (CompetencyArchivedError(), 409, "COMPETENCY_ARCHIVED"),
        (GoalArchivedError(), 409, "GOAL_ARCHIVED"),
        (CompetencyNameExistsError(), 409, "COMPETENCY_NAME_EXISTS"),
        (DatabaseUnavailableError("database unavailable"), 503, "SERVICE_UNAVAILABLE"),
        (pg_errors.QueryCanceled("statement timeout"), 504, "DATABASE_TIMEOUT"),
    ],
)
def test_service_errors_of_the_item_endpoints_become_api_errors(
    client: TestClient,
    service: FakeService,
    call: Any,
    raised: BaseException,
    status: int,
    code: str,
) -> None:
    service.changed = raised

    assert_error(call(client), status, code)


@ITEM_CALLS
def test_the_item_endpoints_require_a_token(service: FakeService, call: Any) -> None:
    # No dependency overrides: the real authentication dependency rejects the request.
    response = call(TestClient(main.app, raise_server_exceptions=False))

    assert_error(response, 401, "UNAUTHORIZED")
    assert response.headers["www-authenticate"] == "Bearer"
    assert service.calls == []
