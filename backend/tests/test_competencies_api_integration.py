"""GET /career-goals/{goal_id}/competencies end to end: HTTP -> JWT -> service -> repository ->
PostgreSQL with Row Level Security (Stage 9.2f, step A).

Nothing between the HTTP request and the database is replaced: the access token is verified by
the real TokenVerifier against a local JWKS server, the service runs a real user_transaction()
as role `authenticated` on the `skillloop_api` login, and RLS applies. Needs TEST_DATABASE_URL and
TEST_DATABASE_ADMIN_URL (see db_integration_helpers); the throwaway users come from the `users`
fixture, which deletes them (and, by cascade, their goals and competencies) afterwards.

The administration connection only PREPARES data (goals, competencies, archiving) and is never
used to make a request. Its writes follow contract L2 (docs/DATABASE-DESIGN.md, section 4.8):
the goal is locked first. Competencies are archived BEFORE their goal, because the row trigger
refuses every write to a competency of an archived goal.

Step B adds POST: the same path, with the request validated, authenticated and written through
the real application. Besides the HTTP answer, every test reads the table through the
administration connection, to prove what was (and what was not) stored.
"""

import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg_pool import ConnectionPool

from app import main
from app.api.deps import get_db_pool, get_token_verifier
from app.models.auth import AuthenticatedUser
from tests.auth_helpers import (
    JWKSServer,
    SigningKey,
    default_claims,
    make_hs256_token_with_public_key,
    make_token,
    make_unsigned_token,
    make_verifier,
)
from tests.db_integration_helpers import (
    TEST_DATABASE_ADMIN_URL,
    TEST_DATABASE_URL,
    open_api_pool,
)
from tests.test_career_goals_api_integration import assert_error, bearer

pytestmark = pytest.mark.skipif(
    not (TEST_DATABASE_URL and TEST_DATABASE_ADMIN_URL),
    reason="TEST_DATABASE_URL and TEST_DATABASE_ADMIN_URL are not both set",
)

BASE = "/api/v1/career-goals"
T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
RESPONSE_KEYS = {
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


@pytest.fixture
def api_pool() -> Iterator[ConnectionPool]:
    pool = open_api_pool(max_size=4, timeout=10)
    try:
        yield pool
    finally:
        pool.close()


@pytest.fixture
def client(jwks_server: JWKSServer, api_pool: ConnectionPool) -> Iterator[TestClient]:
    """The real application with the real verifier (local JWKS) and a real pool."""
    verifier = make_verifier(jwks_server.url)
    main.app.dependency_overrides[get_token_verifier] = lambda: verifier
    main.app.dependency_overrides[get_db_pool] = lambda: api_pool
    try:
        yield TestClient(main.app, raise_server_exceptions=False)
    finally:
        main.app.dependency_overrides.pop(get_token_verifier, None)
        main.app.dependency_overrides.pop(get_db_pool, None)


# --- data preparation (administration connection only) ---------------------------------------


def url(goal_id: UUID) -> str:
    return f"{BASE}/{goal_id}/competencies"


def make_goal(admin: psycopg.Connection, user: AuthenticatedUser) -> UUID:
    row = admin.execute(
        "insert into public.career_goals (user_id, target_role, seniority) "
        "values (%s, 'DevOps Engineer', 'junior') returning id",
        [user.id],
    ).fetchone()
    assert row
    return row[0]


def archive_goal(admin: psycopg.Connection, goal_id: UUID) -> None:
    admin.execute("update public.career_goals set status = 'archived' where id = %s", [goal_id])


def add(
    admin: psycopg.Connection,
    user: AuthenticatedUser,
    goal_id: UUID,
    name: str,
    *,
    display_order: int = 0,
    created_at: datetime = T0,
    archived: bool = False,
) -> UUID:
    """Insert one competency (goal locked first, L2) and optionally archive it."""
    with admin.transaction():
        admin.execute(
            "select 1 from public.career_goals where id = %s for no key update", [goal_id]
        ).fetchall()
        row = admin.execute(
            "insert into public.competencies (career_goal_id, user_id, name, knowledge_type, "
            "importance, expected_level, display_order, created_at) "
            "values (%s, %s, %s, 'application', 'high', 'intermediate', %s, %s) returning id",
            [goal_id, user.id, name, display_order, created_at],
        ).fetchone()
        assert row
        if archived:
            admin.execute(
                "update public.competencies set status = 'archived' where id = %s", [row[0]]
            )
    return row[0]


def names(response: Any) -> list[str]:
    return [item["name"] for item in response.json()["competencies"]]


# --- 1. an empty list ------------------------------------------------------------------------


def test_a_goal_without_competencies_answers_200_and_an_empty_list(
    client: TestClient,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
) -> None:
    user = users[0]
    goal = make_goal(admin, user)

    response = client.get(url(goal), headers=bearer(signing_key, user))

    assert response.status_code == 200
    assert response.json() == {"competencies": []}


# --- 2. active and archived competencies -----------------------------------------------------


def test_the_list_contains_active_and_archived_competencies_with_their_data(
    client: TestClient,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
) -> None:
    user = users[0]
    goal = make_goal(admin, user)
    active_id = add(admin, user, goal, "Docker", display_order=0)
    archived_id = add(admin, user, goal, "Old tool", display_order=1, archived=True)

    response = client.get(url(goal), headers=bearer(signing_key, user))

    assert response.status_code == 200
    items = response.json()["competencies"]
    assert [(i["id"], i["name"], i["status"]) for i in items] == [
        (str(active_id), "Docker", "active"),
        (str(archived_id), "Old tool", "archived"),
    ]
    assert {i["career_goal_id"] for i in items} == {str(goal)}
    first = items[0]
    assert (first["knowledge_type"], first["importance"], first["expected_level"]) == (
        "application",
        "high",
        "intermediate",
    )
    assert (first["description"], first["rationale"], first["category"]) == (None, None, None)
    assert first["created_at"] == "2026-01-01T12:00:00Z"


# --- 3. the order: display_order, created_at, id ---------------------------------------------


def test_the_order_is_display_order_then_created_at_then_id(
    client: TestClient,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
) -> None:
    user = users[0]
    goal = make_goal(admin, user)
    # Inserted in a deliberately "wrong" order, with archived rows in between: the status plays
    # no part in the order.
    add(admin, user, goal, "order-5", display_order=5, created_at=T0)
    add(admin, user, goal, "order-0-late", display_order=0, created_at=T0 + timedelta(hours=2))
    add(
        admin,
        user,
        goal,
        "order-0-early-archived",
        display_order=0,
        created_at=T0 + timedelta(hours=1),
        archived=True,
    )
    add(admin, user, goal, "order-0-first", display_order=0, created_at=T0)

    response = client.get(url(goal), headers=bearer(signing_key, user))

    assert response.status_code == 200
    assert names(response) == [
        "order-0-first",
        "order-0-early-archived",
        "order-0-late",
        "order-5",
    ]


def test_ties_of_display_order_and_created_at_are_broken_by_id(
    client: TestClient,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
) -> None:
    user = users[0]
    goal = make_goal(admin, user)
    ids = [add(admin, user, goal, f"tie-{n}", display_order=3, created_at=T0) for n in range(5)]
    add(admin, user, goal, "after-the-ties", display_order=4, created_at=T0)

    response = client.get(url(goal), headers=bearer(signing_key, user))

    listed = [item["id"] for item in response.json()["competencies"]]
    # PostgreSQL compares uuid bytewise, which is the order of the lowercase hex text.
    assert listed[:5] == sorted(str(i) for i in ids)
    assert response.json()["competencies"][5]["name"] == "after-the-ties"
    # A second request returns exactly the same order (the order is total, not by luck).
    again = client.get(url(goal), headers=bearer(signing_key, user))
    assert again.json() == response.json()


# --- 4. and 5. a goal of another user, a goal that does not exist ----------------------------


def test_the_goal_of_another_user_is_a_404_and_reveals_nothing(
    client: TestClient,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
) -> None:
    user_a, user_b = users[0], users[1]
    goal_a = make_goal(admin, user_a)
    goal_b = make_goal(admin, user_b)
    add(admin, user_a, goal_a, "Owned by A")
    add(admin, user_b, goal_b, "Owned by B (secret)")

    response = client.get(url(goal_b), headers=bearer(signing_key, user_a))

    error = assert_error(response, 404, "GOAL_NOT_FOUND")
    assert "secret" not in response.text and str(user_b.id) not in response.text
    # The same answer as for a goal that does not exist.
    missing = client.get(url(uuid4()), headers=bearer(signing_key, user_a))
    assert assert_error(missing, 404, "GOAL_NOT_FOUND")["message"] == error["message"]
    # The control: each user still reads own data, and only own data.
    assert names(client.get(url(goal_a), headers=bearer(signing_key, user_a))) == ["Owned by A"]
    assert names(client.get(url(goal_b), headers=bearer(signing_key, user_b))) == [
        "Owned by B (secret)"
    ]
    # A goal of A is just as invisible to B.
    foreign = client.get(url(goal_a), headers=bearer(signing_key, user_b))
    assert_error(foreign, 404, "GOAL_NOT_FOUND")


def test_a_goal_that_does_not_exist_is_a_404(
    client: TestClient, signing_key: SigningKey, users: list[AuthenticatedUser]
) -> None:
    response = client.get(url(uuid4()), headers=bearer(signing_key, users[0]))

    assert_error(response, 404, "GOAL_NOT_FOUND")


# --- 6. an archived goal ---------------------------------------------------------------------


def test_an_archived_goal_still_lists_its_competencies(
    client: TestClient,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
) -> None:
    user = users[0]
    goal = make_goal(admin, user)
    add(admin, user, goal, "Kept active", display_order=0)
    add(admin, user, goal, "Kept archived", display_order=1, archived=True)
    archive_goal(admin, goal)  # last: afterwards no competency of the goal can be written

    response = client.get(url(goal), headers=bearer(signing_key, user))

    assert response.status_code == 200
    items = response.json()["competencies"]
    assert [(i["name"], i["status"]) for i in items] == [
        ("Kept active", "active"),
        ("Kept archived", "archived"),
    ]


def test_an_archived_goal_without_competencies_gives_an_empty_list(
    client: TestClient,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
) -> None:
    user = users[0]
    goal = make_goal(admin, user)
    archive_goal(admin, goal)

    response = client.get(url(goal), headers=bearer(signing_key, user))

    assert response.status_code == 200
    assert response.json() == {"competencies": []}


# --- 7. no user_id ---------------------------------------------------------------------------


def test_the_response_never_contains_the_user_id(
    client: TestClient,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
) -> None:
    user = users[0]
    goal = make_goal(admin, user)
    add(admin, user, goal, "One")
    add(admin, user, goal, "Two", display_order=1, archived=True)

    response = client.get(url(goal), headers=bearer(signing_key, user))

    assert response.status_code == 200
    items = response.json()["competencies"]
    assert len(items) == 2
    for item in items:
        assert set(item) == RESPONSE_KEYS
    assert "user_id" not in response.text
    assert str(user.id) not in response.text


# --- 8. no token -----------------------------------------------------------------------------


def test_a_request_without_a_token_is_a_401_and_does_not_reach_the_data(
    client: TestClient,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
) -> None:
    user = users[0]
    goal = make_goal(admin, user)
    add(admin, user, goal, "Hidden without a token")

    response = client.get(url(goal))

    assert_error(response, 401, "UNAUTHORIZED")
    assert response.headers["www-authenticate"] == "Bearer"
    assert "Hidden" not in response.text


# --- 9. a valid token of a user that no longer exists ----------------------------------------


def test_a_valid_token_of_a_deleted_user_gets_goal_not_found_and_not_a_401(
    client: TestClient,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
) -> None:
    """The token is only verified cryptographically (signature, claims), never against
    auth.users, so it stays valid after its user is deleted. The goal and the competencies are
    gone with the user (ON DELETE CASCADE), so the answer is the one for a goal that does not
    exist: 404 GOAL_NOT_FOUND, not 401, and nothing of anyone else."""
    deleted, other = users[0], users[1]
    goal = make_goal(admin, deleted)
    add(admin, deleted, goal, "Owned by the deleted user")
    other_goal = make_goal(admin, other)
    add(admin, other, other_goal, "Secret of another user")
    headers = bearer(signing_key, deleted)  # signed BEFORE the user is deleted
    assert names(client.get(url(goal), headers=headers)) == ["Owned by the deleted user"]

    admin.execute("delete from auth.users where id = %s", [deleted.id])

    # The precondition of the test: the user and everything that belonged to it are gone.
    for table, column, value in (
        ("auth.users", "id", deleted.id),
        ("public.career_goals", "id", goal),
        ("public.competencies", "career_goal_id", goal),
    ):
        count = admin.execute(f"select count(*) from {table} where {column} = %s", [value])
        assert count.fetchone() == (0,), table

    response = client.get(url(goal), headers=headers)

    assert_error(response, 404, "GOAL_NOT_FOUND")
    assert "www-authenticate" not in response.headers
    for leak in ("deleted user", "Secret", str(other.id), str(other_goal)):
        assert leak not in response.text
    # The same token cannot be used to read the goal of another user either.
    assert_error(client.get(url(other_goal), headers=headers), 404, "GOAL_NOT_FOUND")
    # The control: the data of the other user is untouched and still readable by its owner.
    assert names(client.get(url(other_goal), headers=bearer(signing_key, other))) == [
        "Secret of another user"
    ]


# =============================================================================================
# POST /career-goals/{goal_id}/competencies
# =============================================================================================

REQUIRED = {
    "name": "Terraform",
    "knowledge_type": "application",
    "importance": "high",
    "expected_level": "intermediate",
}
FULL = {
    **REQUIRED,
    "description": "Infrastructure as Code.",
    "rationale": "Infrastructure is code.",
    "category": "IaC",
    "display_order": 7,
}
MAX_DISPLAY_ORDER = 2_147_483_647  # the largest PostgreSQL integer
CANARY = "CANARY-b7d2e"  # a submitted value that must never be echoed back

ROW_COLUMNS = (
    "name, description, rationale, category, knowledge_type, importance, expected_level, "
    "display_order, status, user_id, career_goal_id, id"
)


def stored(admin: psycopg.Connection, goal_id: UUID) -> list[tuple[Any, ...]]:
    """Every competency row of the goal as the database holds it (any status, any owner)."""
    return admin.execute(
        f"select {ROW_COLUMNS} from public.competencies where career_goal_id = %s "
        "order by created_at, id",
        [goal_id],
    ).fetchall()


def total(admin: psycopg.Connection, users: list[AuthenticatedUser]) -> int:
    """How many competencies exist for the throwaway users (all goals, all statuses)."""
    row = admin.execute(
        "select count(*) from public.competencies where user_id = any(%s)",
        [[u.id for u in users]],
    ).fetchone()
    assert row
    return row[0]


def with_(**changes: Any) -> dict[str, Any]:
    return {**REQUIRED, **changes}


def without(field: str) -> dict[str, Any]:
    return {k: v for k, v in REQUIRED.items() if k != field}


# --- A. creating a competency ----------------------------------------------------------------


def test_post_a_minimal_request_creates_an_active_competency_with_the_defaults(
    client: TestClient,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
) -> None:
    user = users[0]
    goal = make_goal(admin, user)

    response = client.post(url(goal), json=REQUIRED, headers=bearer(signing_key, user))

    assert response.status_code == 201
    body = response.json()
    assert set(body) == RESPONSE_KEYS
    assert body["career_goal_id"] == str(goal)
    assert (body["name"], body["knowledge_type"], body["importance"]) == (
        "Terraform",
        "application",
        "high",
    )
    assert body["expected_level"] == "intermediate"
    assert (body["description"], body["rationale"], body["category"]) == (None, None, None)
    assert (body["display_order"], body["status"]) == (0, "active")
    assert body["created_at"] == body["updated_at"]
    assert datetime.fromisoformat(body["created_at"]).tzinfo is not None
    # the owner is never shown ...
    assert "user_id" not in response.text and str(user.id) not in response.text
    # ... but it is stored: the user of the token, in the goal of the path
    assert stored(admin, goal) == [
        (
            "Terraform",
            None,
            None,
            None,
            "application",
            "high",
            "intermediate",
            0,
            "active",
            user.id,
            goal,
            UUID(body["id"]),
        )
    ]


def test_post_a_full_request_stores_every_field(
    client: TestClient,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
) -> None:
    user = users[0]
    goal = make_goal(admin, user)

    response = client.post(url(goal), json=FULL, headers=bearer(signing_key, user))

    assert response.status_code == 201
    body = response.json()
    assert set(body) == RESPONSE_KEYS
    assert {k: body[k] for k in FULL} == FULL
    assert (body["status"], body["career_goal_id"]) == ("active", str(goal))
    assert "user_id" not in response.text
    assert stored(admin, goal) == [
        (
            "Terraform",
            "Infrastructure as Code.",
            "Infrastructure is code.",
            "IaC",
            "application",
            "high",
            "intermediate",
            7,
            "active",
            user.id,
            goal,
            UUID(body["id"]),
        )
    ]


@pytest.mark.parametrize(
    ("knowledge_type", "importance", "expected_level"),
    [
        ("memory", "critical", "beginner"),
        ("application", "high", "intermediate"),
        ("documentation", "medium", "advanced"),
        ("memory", "low", "advanced"),
    ],
)
def test_post_accepts_every_allowed_enum_value(
    client: TestClient,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
    knowledge_type: str,
    importance: str,
    expected_level: str,
) -> None:
    user = users[0]
    goal = make_goal(admin, user)
    body = with_(
        knowledge_type=knowledge_type, importance=importance, expected_level=expected_level
    )

    response = client.post(url(goal), json=body, headers=bearer(signing_key, user))

    assert response.status_code == 201
    (row,) = stored(admin, goal)
    assert row[4:7] == (knowledge_type, importance, expected_level)


def test_post_trims_the_text_and_stores_blank_optional_texts_as_null(
    client: TestClient,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
) -> None:
    user = users[0]
    goal = make_goal(admin, user)
    body = with_(name="  Terraform \n", description="   ", rationale="\n\t", category="")

    response = client.post(url(goal), json=body, headers=bearer(signing_key, user))

    assert response.status_code == 201
    created = response.json()
    assert created["name"] == "Terraform"
    assert (created["description"], created["rationale"], created["category"]) == (None,) * 3
    (row,) = stored(admin, goal)
    assert row[:4] == ("Terraform", None, None, None)


def test_post_accepts_the_smallest_and_the_largest_display_order(
    client: TestClient,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
) -> None:
    user = users[0]
    goal = make_goal(admin, user)
    headers = bearer(signing_key, user)

    smallest = client.post(url(goal), json=with_(name="First", display_order=0), headers=headers)
    largest = client.post(
        url(goal), json=with_(name="Last", display_order=MAX_DISPLAY_ORDER), headers=headers
    )

    assert (smallest.status_code, largest.status_code) == (201, 201)
    assert largest.json()["display_order"] == MAX_DISPLAY_ORDER
    assert [(row[0], row[7]) for row in stored(admin, goal)] == [
        ("First", 0),
        ("Last", MAX_DISPLAY_ORDER),
    ]


def test_post_accepts_the_longest_allowed_texts_and_counts_characters_not_bytes(
    client: TestClient,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
) -> None:
    """The limits of the schema and the CHECK constraints of the table agree at the boundary."""
    user = users[0]
    goal = make_goal(admin, user)
    headers = bearer(signing_key, user)
    longest = with_(name="n" * 100, description="d" * 1000, rationale="r" * 1000, category="c" * 60)
    emoji = with_(name="😀" * 100, description="ł" * 1000)  # 400 and 2000 bytes in UTF-8

    first = client.post(url(goal), json=longest, headers=headers)
    second = client.post(url(goal), json=emoji, headers=headers)

    assert (first.status_code, second.status_code) == (201, 201)
    rows = stored(admin, goal)
    assert [(len(r[0]), len(r[1] or ""), len(r[2] or ""), len(r[3] or "")) for r in rows] == [
        (100, 1000, 1000, 60),
        (100, 1000, 0, 0),
    ]


def test_the_created_competency_is_returned_by_get(
    client: TestClient,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
) -> None:
    user = users[0]
    goal = make_goal(admin, user)
    headers = bearer(signing_key, user)

    created = client.post(url(goal), json=FULL, headers=headers)
    listed = client.get(url(goal), headers=headers)

    assert created.status_code == 201 and listed.status_code == 200
    assert listed.json() == {"competencies": [created.json()]}


# --- B. request validation: 422 and nothing is written ---------------------------------------

INVALID_BODIES = [
    *[pytest.param(without(f), f, id=f"{f}-missing") for f in REQUIRED],
    *[pytest.param(with_(**{f: None}), f, id=f"{f}-null") for f in REQUIRED],
    pytest.param(with_(name=""), "name", id="name-empty"),
    pytest.param(with_(name="   "), "name", id="name-spaces"),
    pytest.param(with_(name="\t\n"), "name", id="name-tab-and-newline"),
    pytest.param(with_(name="x" * 101), "name", id="name-101-characters"),
    pytest.param(with_(name="😀" * 101), "name", id="name-101-emoji"),
    pytest.param(with_(name=5), "name", id="name-number"),
    pytest.param(with_(name=["Docker"]), "name", id="name-list"),
    pytest.param(with_(name="a\x00b"), "name", id="name-NUL"),
    pytest.param(with_(knowledge_type="magic"), "knowledge_type", id="knowledge_type-unknown"),
    pytest.param(with_(knowledge_type="Memory"), "knowledge_type", id="knowledge_type-case"),
    pytest.param(with_(knowledge_type=5), "knowledge_type", id="knowledge_type-number"),
    pytest.param(with_(importance="urgent"), "importance", id="importance-unknown"),
    pytest.param(with_(importance="HIGH"), "importance", id="importance-case"),
    pytest.param(with_(expected_level="godlike"), "expected_level", id="level-unknown"),
    pytest.param(with_(expected_level="not_assessed"), "expected_level", id="level-not_assessed"),
    pytest.param(with_(description="d" * 1001), "description", id="description-1001"),
    pytest.param(with_(rationale="r" * 1001), "rationale", id="rationale-1001"),
    pytest.param(with_(category="c" * 61), "category", id="category-61"),
    pytest.param(with_(description=5), "description", id="description-number"),
    pytest.param(with_(rationale=True), "rationale", id="rationale-bool"),
    pytest.param(with_(category=["a"]), "category", id="category-list"),
    pytest.param(with_(description="a\x00b"), "description", id="description-NUL"),
    pytest.param(with_(rationale="a\x00b"), "rationale", id="rationale-NUL"),
    pytest.param(with_(category="a\x00b"), "category", id="category-NUL"),
    pytest.param(with_(display_order=-1), "display_order", id="display_order-negative"),
    pytest.param(
        with_(display_order=MAX_DISPLAY_ORDER + 1), "display_order", id="display_order-too-big"
    ),
    pytest.param(with_(display_order=10**30), "display_order", id="display_order-huge"),
    pytest.param(with_(display_order="first"), "display_order", id="display_order-text"),
    pytest.param(with_(display_order="5"), "display_order", id="display_order-numeric-string"),
    pytest.param(with_(display_order=5.0), "display_order", id="display_order-float-integral"),
    pytest.param(with_(display_order=5.5), "display_order", id="display_order-float"),
    pytest.param(with_(display_order=True), "display_order", id="display_order-true"),
    pytest.param(with_(display_order=False), "display_order", id="display_order-false"),
    pytest.param(with_(display_order=None), "display_order", id="display_order-null"),
    pytest.param(with_(display_order=[1]), "display_order", id="display_order-list"),
    *[
        pytest.param(with_(**{f: "x"}), f, id=f"unknown-field-{f}")
        for f in (
            "id",
            "career_goal_id",
            "user_id",
            "status",
            "created_at",
            "updated_at",
            "current_level",
            "source",
            "unknown",
        )
    ],
]


@pytest.mark.parametrize(("body", "field"), INVALID_BODIES)
def test_post_rejects_an_invalid_request_with_422_and_writes_nothing(
    client: TestClient,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
    body: dict[str, Any],
    field: str,
) -> None:
    user = users[0]
    goal = make_goal(admin, user)

    response = client.post(url(goal), json=body, headers=bearer(signing_key, user))

    error = assert_error(response, 422, "VALIDATION_ERROR")
    assert field in {detail["field"] for detail in error["details"]}
    assert stored(admin, goal) == []
    assert total(admin, users) == 0


@pytest.mark.parametrize(
    "send",
    [
        pytest.param(lambda c, u, h: c.post(u, json=["Terraform"], headers=h), id="json-array"),
        pytest.param(lambda c, u, h: c.post(u, json="Terraform", headers=h), id="json-string"),
        pytest.param(lambda c, u, h: c.post(u, json=42, headers=h), id="json-number"),
        pytest.param(
            lambda c, u, h: c.post(
                u, content=b"null", headers={**h, "Content-Type": "application/json"}
            ),
            id="json-null",
        ),
        pytest.param(lambda c, u, h: c.post(u, headers=h), id="no-body"),
        pytest.param(
            lambda c, u, h: c.post(
                u, content=b"{not json", headers={**h, "Content-Type": "application/json"}
            ),
            id="malformed-json",
        ),
    ],
)
def test_post_rejects_a_body_that_is_not_a_json_object_with_422(
    client: TestClient,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
    send: Any,
) -> None:
    user = users[0]
    goal = make_goal(admin, user)

    response = send(client, url(goal), bearer(signing_key, user))

    assert_error(response, 422, "VALIDATION_ERROR")
    assert stored(admin, goal) == []


def test_post_with_a_malformed_goal_id_is_422(
    client: TestClient,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
) -> None:
    user = users[0]
    make_goal(admin, user)

    response = client.post(
        f"{BASE}/not-a-uuid/competencies", json=REQUIRED, headers=bearer(signing_key, user)
    )

    error = assert_error(response, 422, "VALIDATION_ERROR")
    assert "goal_id" in {detail["field"] for detail in error["details"]}
    assert total(admin, users) == 0


def test_a_rejected_value_is_not_echoed_in_the_validation_error(
    client: TestClient,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
) -> None:
    user = users[0]
    goal = make_goal(admin, user)
    body = with_(importance=CANARY, display_order=CANARY, category="c" * 61, user_id=CANARY)

    response = client.post(url(goal), json=body, headers=bearer(signing_key, user))

    error = assert_error(response, 422, "VALIDATION_ERROR")
    assert {d["field"] for d in error["details"]} >= {"importance", "display_order", "category"}
    assert CANARY not in response.text


def test_validation_comes_before_the_goal_is_looked_up(
    client: TestClient,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
) -> None:
    """An invalid request is a 422 whatever the goal is: it reveals nothing about goals."""
    user_a, user_b = users[0], users[1]
    goal_b = make_goal(admin, user_b)
    headers = bearer(signing_key, user_a)
    bad = with_(importance="urgent")

    for goal in (uuid4(), goal_b):
        assert_error(client.post(url(goal), json=bad, headers=headers), 422, "VALIDATION_ERROR")

    assert total(admin, users) == 0


# --- C. authentication and authorisation -----------------------------------------------------


def test_post_without_a_token_is_401_and_writes_nothing(
    client: TestClient,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
) -> None:
    goal = make_goal(admin, users[0])

    response = client.post(url(goal), json=REQUIRED)

    assert_error(response, 401, "UNAUTHORIZED")
    assert response.headers["www-authenticate"] == "Bearer"
    assert stored(admin, goal) == []


@pytest.mark.parametrize(
    "build",
    [
        pytest.param(lambda key, user: "not-a-token", id="garbage"),
        pytest.param(
            lambda key, user: make_token(
                key, default_claims(sub=str(user.id), exp=int(time.time()) - 3600)
            ),
            id="expired",
        ),
        pytest.param(
            lambda key, user: make_token(SigningKey(kid=key.kid), default_claims(sub=str(user.id))),
            id="signed-by-another-key",
        ),
        pytest.param(
            lambda key, user: make_token(key, default_claims(sub=str(user.id)), kid="unknown"),
            id="unknown-key-id",
        ),
        pytest.param(
            lambda key, user: make_unsigned_token(default_claims(sub=str(user.id)), kid=key.kid),
            id="alg-none",
        ),
        pytest.param(
            lambda key, user: make_hs256_token_with_public_key(
                key, default_claims(sub=str(user.id))
            ),
            id="hs256-confusion",
        ),
        pytest.param(
            lambda key, user: make_token(key, default_claims(sub=str(user.id), aud="other")),
            id="wrong-audience",
        ),
        pytest.param(
            lambda key, user: make_token(key, default_claims(sub=str(user.id), role="anon")),
            id="role-anon",
        ),
        pytest.param(
            lambda key, user: make_token(key, default_claims(sub="not-a-uuid")),
            id="subject-not-a-uuid",
        ),
    ],
)
def test_post_with_an_invalid_token_is_401_and_writes_nothing(
    client: TestClient,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
    build: Any,
) -> None:
    user = users[0]
    goal = make_goal(admin, user)

    response = client.post(
        url(goal), json=REQUIRED, headers={"Authorization": f"Bearer {build(signing_key, user)}"}
    )

    assert_error(response, 401, "UNAUTHORIZED")
    assert response.headers["www-authenticate"] == "Bearer"
    assert stored(admin, goal) == []


def test_post_with_a_token_of_another_scheme_is_401_and_writes_nothing(
    client: TestClient,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
) -> None:
    goal = make_goal(admin, users[0])

    response = client.post(url(goal), json=REQUIRED, headers={"Authorization": "Basic dXNlcjpw"})

    assert_error(response, 401, "UNAUTHORIZED")
    assert stored(admin, goal) == []


def test_post_to_a_goal_that_does_not_exist_is_404_and_writes_nothing(
    client: TestClient,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
) -> None:
    make_goal(admin, users[0])

    response = client.post(url(uuid4()), json=REQUIRED, headers=bearer(signing_key, users[0]))

    assert_error(response, 404, "GOAL_NOT_FOUND")
    assert total(admin, users) == 0


def test_post_to_an_archived_goal_is_409_and_the_goal_stays_frozen(
    client: TestClient,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
) -> None:
    user = users[0]
    headers = bearer(signing_key, user)
    old_goal = make_goal(admin, user)
    add(admin, user, old_goal, "Frozen")
    archive_goal(admin, old_goal)
    before = stored(admin, old_goal)

    refused = client.post(url(old_goal), json=REQUIRED, headers=headers)

    assert_error(refused, 409, "GOAL_ARCHIVED")
    assert stored(admin, old_goal) == before
    # The user may have a NEW active goal meanwhile: it accepts competencies, the old one still
    # does not (the database tells the two goals apart, not the user).
    new_goal = make_goal(admin, user)
    accepted = client.post(url(new_goal), json=REQUIRED, headers=headers)
    refused_again = client.post(url(old_goal), json=with_(name="Another"), headers=headers)

    assert accepted.status_code == 201
    assert_error(refused_again, 409, "GOAL_ARCHIVED")
    assert [row[0] for row in stored(admin, new_goal)] == ["Terraform"]
    assert stored(admin, old_goal) == before


def test_post_to_an_archived_goal_without_any_competency_is_409(
    client: TestClient,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
) -> None:
    user = users[0]
    goal = make_goal(admin, user)
    archive_goal(admin, goal)

    response = client.post(url(goal), json=REQUIRED, headers=bearer(signing_key, user))

    assert_error(response, 409, "GOAL_ARCHIVED")
    assert stored(admin, goal) == []


@pytest.mark.parametrize("owner", ["own", "other"])
def test_post_rejects_a_user_id_in_the_body_whoever_it_names(
    client: TestClient,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
    owner: str,
) -> None:
    user_a, user_b = users[0], users[1]
    goal_a = make_goal(admin, user_a)
    goal_b = make_goal(admin, user_b)
    named = user_a if owner == "own" else user_b
    body = with_(user_id=str(named.id), career_goal_id=str(goal_b))

    # at the goal of A, and at the goal of B, with the token of A
    for goal in (goal_a, goal_b):
        response = client.post(url(goal), json=body, headers=bearer(signing_key, user_a))
        error = assert_error(response, 422, "VALIDATION_ERROR")
        assert {"user_id", "career_goal_id"} <= {d["field"] for d in error["details"]}

    assert total(admin, users) == 0


def test_a_valid_token_of_a_user_that_never_existed_cannot_write_into_a_goal_of_another_user(
    client: TestClient,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
) -> None:
    """Impersonation attempt: a correctly signed token for a made-up user id, aimed at the goal
    of a real user. The database sees a different user than the owner of the goal."""
    victim = users[0]
    goal = make_goal(admin, victim)
    add(admin, victim, goal, "Existing")
    before = stored(admin, goal)
    stranger = AuthenticatedUser(id=uuid4())

    response = client.post(url(goal), json=REQUIRED, headers=bearer(signing_key, stranger))

    assert_error(response, 404, "GOAL_NOT_FOUND")
    assert stored(admin, goal) == before
    assert str(victim.id) not in response.text


def test_a_valid_token_of_a_deleted_user_cannot_create_a_competency(
    client: TestClient,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
) -> None:
    deleted, other = users[0], users[1]
    goal = make_goal(admin, deleted)
    other_goal = make_goal(admin, other)
    headers = bearer(signing_key, deleted)  # signed BEFORE the user is deleted
    assert client.post(url(goal), json=REQUIRED, headers=headers).status_code == 201
    admin.execute("delete from auth.users where id = %s", [deleted.id])

    own = client.post(url(goal), json=with_(name="After"), headers=headers)
    foreign = client.post(url(other_goal), json=with_(name="After"), headers=headers)

    assert_error(own, 404, "GOAL_NOT_FOUND")
    assert "www-authenticate" not in own.headers  # not a 401
    assert_error(foreign, 404, "GOAL_NOT_FOUND")
    assert stored(admin, goal) == []
    assert stored(admin, other_goal) == []


# --- D. isolation between users --------------------------------------------------------------


def test_a_user_cannot_create_a_competency_in_the_goal_of_another_user(
    client: TestClient,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
) -> None:
    user_a, user_b = users[0], users[1]
    goal_a = make_goal(admin, user_a)
    goal_b = make_goal(admin, user_b)
    add(admin, user_a, goal_a, "Owned by A")
    add(admin, user_b, goal_b, "Owned by B (secret)")
    before_a, before_b = stored(admin, goal_a), stored(admin, goal_b)

    response = client.post(url(goal_b), json=REQUIRED, headers=bearer(signing_key, user_a))

    error = assert_error(response, 404, "GOAL_NOT_FOUND")
    # nothing about B in the answer, and the same answer as for a goal that does not exist
    for leak in ("secret", "Owned by B", str(user_b.id), str(goal_b)):
        assert leak not in response.text
    missing = client.post(url(uuid4()), json=REQUIRED, headers=bearer(signing_key, user_a))
    assert assert_error(missing, 404, "GOAL_NOT_FOUND")["message"] == error["message"]
    # nothing was written anywhere
    assert (stored(admin, goal_a), stored(admin, goal_b)) == (before_a, before_b)
    # and B still reads own data, only own data
    listed = client.get(url(goal_b), headers=bearer(signing_key, user_b))
    assert names(listed) == ["Owned by B (secret)"]
    assert names(client.get(url(goal_a), headers=bearer(signing_key, user_a))) == ["Owned by A"]


def test_two_users_can_each_create_a_competency_with_the_same_name_in_their_own_goals(
    client: TestClient,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
) -> None:
    user_a, user_b = users[0], users[1]
    goal_a = make_goal(admin, user_a)
    goal_b = make_goal(admin, user_b)

    created_a = client.post(url(goal_a), json=REQUIRED, headers=bearer(signing_key, user_a))
    created_b = client.post(url(goal_b), json=REQUIRED, headers=bearer(signing_key, user_b))

    assert (created_a.status_code, created_b.status_code) == (201, 201)
    assert created_a.json()["id"] != created_b.json()["id"]
    assert [(r[0], r[9], r[10]) for r in stored(admin, goal_a)] == [
        ("Terraform", user_a.id, goal_a)
    ]
    assert [(r[0], r[9], r[10]) for r in stored(admin, goal_b)] == [
        ("Terraform", user_b.id, goal_b)
    ]
    for user, goal, created in ((user_a, goal_a, created_a), (user_b, goal_b, created_b)):
        listed = client.get(url(goal), headers=bearer(signing_key, user))
        assert listed.json() == {"competencies": [created.json()]}


# --- E. the rules of the database, seen through HTTP -----------------------------------------


def test_post_a_name_that_is_taken_is_409_whatever_its_case_and_spacing(
    client: TestClient,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
) -> None:
    user = users[0]
    goal = make_goal(admin, user)
    headers = bearer(signing_key, user)
    assert client.post(url(goal), json=with_(name="Docker"), headers=headers).status_code == 201

    for name in ("Docker", "docker", "DOCKER", "  Docker  ", " dOCKER\n"):
        response = client.post(url(goal), json=with_(name=name), headers=headers)
        assert_error(response, 409, "COMPETENCY_NAME_EXISTS")

    assert [row[0] for row in stored(admin, goal)] == ["Docker"]


def test_an_archived_competency_releases_its_name(
    client: TestClient,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
) -> None:
    user = users[0]
    goal = make_goal(admin, user)
    add(admin, user, goal, "Docker", archived=True)

    response = client.post(url(goal), json=with_(name="docker"), headers=bearer(signing_key, user))

    assert response.status_code == 201
    assert [(row[0], row[8]) for row in stored(admin, goal)] == [
        ("Docker", "archived"),
        ("docker", "active"),
    ]


def test_the_fiftieth_active_competency_is_created_and_the_fifty_first_is_409(
    client: TestClient,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
) -> None:
    user = users[0]
    goal = make_goal(admin, user)
    headers = bearer(signing_key, user)
    for n in range(49):
        add(admin, user, goal, f"filler-{n}")
    # archived competencies do not count towards the limit
    for n in range(5):
        add(admin, user, goal, f"archived-{n}", archived=True)

    fiftieth = client.post(url(goal), json=with_(name="Fiftieth"), headers=headers)
    refused = client.post(url(goal), json=with_(name="Fifty-first"), headers=headers)

    assert fiftieth.status_code == 201
    assert_error(refused, 409, "COMPETENCY_LIMIT_REACHED")
    rows = stored(admin, goal)
    assert sum(1 for row in rows if row[8] == "active") == 50
    assert "Fifty-first" not in [row[0] for row in rows]


def test_the_limit_is_reported_before_a_taken_name(
    client: TestClient,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
) -> None:
    """A request that breaks both rules answers COMPETENCY_LIMIT_REACHED (docs/API-DESIGN.md,
    section 7): the database checks the goal and the limit before the unique index."""
    user = users[0]
    goal = make_goal(admin, user)
    for n in range(50):
        add(admin, user, goal, f"filler-{n}")

    response = client.post(
        url(goal), json=with_(name="filler-3"), headers=bearer(signing_key, user)
    )

    assert_error(response, 409, "COMPETENCY_LIMIT_REACHED")


def test_the_limit_of_one_goal_does_not_affect_another_users_goal(
    client: TestClient,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
) -> None:
    user_a, user_b = users[0], users[1]
    goal_a = make_goal(admin, user_a)
    goal_b = make_goal(admin, user_b)
    for n in range(50):
        add(admin, user_a, goal_a, f"filler-{n}")

    full = client.post(url(goal_a), json=REQUIRED, headers=bearer(signing_key, user_a))
    free = client.post(url(goal_b), json=REQUIRED, headers=bearer(signing_key, user_b))

    assert_error(full, 409, "COMPETENCY_LIMIT_REACHED")
    assert free.status_code == 201
