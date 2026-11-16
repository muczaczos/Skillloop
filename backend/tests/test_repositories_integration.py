"""Repositories against a REAL local PostgreSQL / Supavisor with real Row Level Security.

Every call goes through user_transaction(), exactly as the services will do it. Needs
TEST_DATABASE_URL and TEST_DATABASE_ADMIN_URL (see db_integration_helpers); the throwaway
users come from the `users` fixture (conftest.py).
"""

import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg import errors
from psycopg_pool import ConnectionPool

from app.db.session import user_transaction
from app.models.auth import AuthenticatedUser
from app.repositories import career_goals, profiles
from app.schemas.career_goal import CareerGoalCreate
from app.schemas.common import (
    DISPLAY_NAME_MAX_LENGTH,
    GOAL_DESCRIPTION_MAX_LENGTH,
    TARGET_ROLE_MAX_LENGTH,
)
from app.schemas.profile import ProfileUpdate
from tests.db_integration_helpers import (
    TEST_DATABASE_ADMIN_URL,
    TEST_DATABASE_URL,
    open_api_pool,
)

pytestmark = pytest.mark.skipif(
    not (TEST_DATABASE_URL and TEST_DATABASE_ADMIN_URL),
    reason="TEST_DATABASE_URL and TEST_DATABASE_ADMIN_URL are not both set",
)


def new_goal(conn: psycopg.Connection, user: AuthenticatedUser, **overrides: Any) -> Any:
    values: dict[str, Any] = {"target_role": "DevOps Engineer", "seniority": "junior"}
    values.update({"description": None, **overrides})
    return career_goals.insert(conn, user.id, **values)


def admin_goal(admin: psycopg.Connection, goal_id: UUID) -> tuple[Any, ...] | None:
    cur = admin.execute(
        "select target_role, seniority, description, status from public.career_goals where id = %s",
        [goal_id],
    )
    return cur.fetchone()


def admin_profile_name(admin: psycopg.Connection, user: AuthenticatedUser) -> str | None:
    cur = admin.execute("select display_name from public.profiles where id = %s", [user.id])
    return cur.fetchone()[0]  # type: ignore[index]


def admin_create_goal(
    admin: psycopg.Connection, user: AuthenticatedUser, status: str = "active"
) -> UUID:
    cur = admin.execute(
        "insert into public.career_goals (user_id, target_role, seniority, status) "
        "values (%s, 'Prepared by admin', 'senior', %s) returning id",
        [user.id, status],
    )
    return cur.fetchone()[0]  # type: ignore[index]


# --- profiles ----------------------------------------------------------------------------------


def test_profile_get_returns_the_profile_created_by_the_trigger(
    pool: ConnectionPool, users: list[AuthenticatedUser]
) -> None:
    with user_transaction(pool, users[0]) as conn:
        record = profiles.get(conn, users[0].id)

    assert record is not None
    assert record.id == users[0].id
    assert (record.display_name, record.preferred_language) == (None, "en")
    assert record.created_at.tzinfo is not None and record.updated_at.tzinfo is not None


def test_profile_of_another_user_is_not_returned(
    pool: ConnectionPool, users: list[AuthenticatedUser]
) -> None:
    with user_transaction(pool, users[0]) as conn:
        assert profiles.get(conn, users[1].id) is None
        assert profiles.get(conn, uuid4()) is None


def test_profile_update_changes_only_the_given_columns_and_returns_the_row(
    pool: ConnectionPool, users: list[AuthenticatedUser]
) -> None:
    with user_transaction(pool, users[0]) as conn:
        before = profiles.get(conn, users[0].id)
        named = profiles.update(conn, users[0].id, {"display_name": "Ann"})
        polish = profiles.update(conn, users[0].id, {"preferred_language": "pl"})
        cleared = profiles.update(conn, users[0].id, {"display_name": None})
        both = profiles.update(
            conn, users[0].id, {"display_name": "Bea", "preferred_language": "en"}
        )

    assert before and named and polish and cleared and both
    assert (named.display_name, named.preferred_language) == ("Ann", "en")
    assert (polish.display_name, polish.preferred_language) == ("Ann", "pl")  # name untouched
    assert (cleared.display_name, cleared.preferred_language) == (None, "pl")  # null clears
    assert (both.display_name, both.preferred_language) == ("Bea", "en")
    assert both.id == before.id and both.created_at == before.created_at
    assert both.updated_at > before.updated_at


def test_profile_update_of_another_user_changes_nothing(
    pool: ConnectionPool, users: list[AuthenticatedUser], admin: psycopg.Connection
) -> None:
    with user_transaction(pool, users[0]) as conn:
        assert profiles.update(conn, users[1].id, {"display_name": "Hacked"}) is None

    assert admin_profile_name(admin, users[1]) is None


@pytest.mark.parametrize("changes", [{}, {"id": "x"}, {"user_id": "x"}, {"created_at": "x"}])
def test_profile_update_refuses_forbidden_columns_before_sql(
    pool: ConnectionPool, users: list[AuthenticatedUser], changes: dict[str, Any]
) -> None:
    with pytest.raises(ValueError), user_transaction(pool, users[0]) as conn:
        profiles.update(conn, users[0].id, changes)


def test_profile_update_accepts_what_the_schema_produces(
    pool: ConnectionPool, users: list[AuthenticatedUser]
) -> None:
    changes = ProfileUpdate.model_validate(
        {"display_name": "  Ann  ", "preferred_language": "pl"}
    ).changes()

    with user_transaction(pool, users[0]) as conn:
        record = profiles.update(conn, users[0].id, changes)

    assert record is not None and (record.display_name, record.preferred_language) == ("Ann", "pl")


# --- career goals ------------------------------------------------------------------------------


def test_goal_insert_returns_the_complete_active_record(
    pool: ConnectionPool, users: list[AuthenticatedUser]
) -> None:
    with user_transaction(pool, users[0]) as conn:
        goal = new_goal(conn, users[0], description="Prepare for the role.")

    assert goal.user_id == users[0].id
    assert (goal.target_role, goal.seniority, goal.status) == (
        "DevOps Engineer",
        "junior",
        "active",
    )
    assert goal.description == "Prepare for the role."
    assert goal.created_at.tzinfo is not None and goal.updated_at >= goal.created_at


def test_a_second_active_goal_violates_the_unique_index(
    pool: ConnectionPool, users: list[AuthenticatedUser]
) -> None:
    with user_transaction(pool, users[0]) as conn:
        new_goal(conn, users[0])

    with (
        pytest.raises(errors.UniqueViolation) as exc_info,
        user_transaction(pool, users[0]) as conn,
    ):
        new_goal(conn, users[0], target_role="Another")

    # Facts the HTTP error mapping (stage 6.4) relies on:
    assert exc_info.value.sqlstate == "23505"
    assert exc_info.value.diag.constraint_name == "one_active_goal_per_user"


def test_insert_for_another_user_is_rejected_by_row_level_security(
    pool: ConnectionPool, users: list[AuthenticatedUser]
) -> None:
    with (
        pytest.raises(errors.InsufficientPrivilege) as exc_info,
        user_transaction(pool, users[0]) as conn,
    ):
        career_goals.insert(
            conn, users[1].id, target_role="Spoofed", seniority="mid", description=None
        )

    assert exc_info.value.sqlstate == "42501"


def test_get_by_id_returns_own_goals_in_any_status_and_nothing_else(
    pool: ConnectionPool, users: list[AuthenticatedUser], admin: psycopg.Connection
) -> None:
    foreign = admin_create_goal(admin, users[1])
    with user_transaction(pool, users[0]) as conn:
        mine = new_goal(conn, users[0])
        archived = career_goals.archive_active(conn, users[0].id, mine.id)

    with user_transaction(pool, users[0]) as conn:
        assert career_goals.get_by_id(conn, users[0].id, mine.id) == archived  # archived is visible
        assert career_goals.get_by_id(conn, users[0].id, foreign) is None  # not theirs
        assert career_goals.get_by_id(conn, users[0].id, uuid4()) is None  # does not exist
        assert career_goals.get_by_id(conn, users[1].id, mine.id) is None  # wrong owner argument


def test_get_active_follows_the_goal_life_cycle(
    pool: ConnectionPool, users: list[AuthenticatedUser]
) -> None:
    with user_transaction(pool, users[0]) as conn:
        assert career_goals.get_active(conn, users[0].id) is None
        goal = new_goal(conn, users[0])
        assert career_goals.get_active(conn, users[0].id) == goal
        career_goals.archive_active(conn, users[0].id, goal.id)
        assert career_goals.get_active(conn, users[0].id) is None


def test_update_active_changes_only_the_given_columns(
    pool: ConnectionPool, users: list[AuthenticatedUser]
) -> None:
    with user_transaction(pool, users[0]) as conn:
        goal = new_goal(conn, users[0], description="Old")
        role = career_goals.update_active(conn, users[0].id, goal.id, {"target_role": "SRE"})
        level = career_goals.update_active(conn, users[0].id, goal.id, {"seniority": "senior"})
        cleared = career_goals.update_active(conn, users[0].id, goal.id, {"description": None})
        both = career_goals.update_active(
            conn, users[0].id, goal.id, {"target_role": "Platform", "description": "New"}
        )

    assert role and level and cleared and both
    assert (role.target_role, role.seniority, role.description) == ("SRE", "junior", "Old")
    assert (level.target_role, level.seniority, level.description) == ("SRE", "senior", "Old")
    assert (cleared.target_role, cleared.description) == ("SRE", None)
    assert (both.target_role, both.seniority, both.description) == ("Platform", "senior", "New")
    assert both.status == "active" and both.created_at == goal.created_at
    assert both.updated_at > goal.updated_at


@pytest.mark.parametrize("changes", [{}, {"status": "archived"}, {"user_id": "x"}, {"id": "x"}])
def test_update_active_refuses_forbidden_columns_before_sql(
    pool: ConnectionPool, users: list[AuthenticatedUser], changes: dict[str, Any]
) -> None:
    with pytest.raises(ValueError), user_transaction(pool, users[0]) as conn:
        goal = new_goal(conn, users[0])
        career_goals.update_active(conn, users[0].id, goal.id, changes)


def test_archive_returns_the_archived_goal_once_and_keeps_the_history(
    pool: ConnectionPool, users: list[AuthenticatedUser]
) -> None:
    with user_transaction(pool, users[0]) as conn:
        first = new_goal(conn, users[0])
        archived = career_goals.archive_active(conn, users[0].id, first.id)
        again = career_goals.archive_active(conn, users[0].id, first.id)
        second = new_goal(conn, users[0], target_role="Next goal")  # a new active goal is allowed
        still_there = career_goals.get_by_id(conn, users[0].id, first.id)

    assert archived is not None and archived.status == "archived"
    assert archived.updated_at > first.updated_at
    assert again is None  # already archived: nothing to do
    assert second.status == "active" and second.id != first.id
    assert still_there == archived  # the history is kept


def test_an_archived_goal_is_immutable_and_cannot_be_restored(
    pool: ConnectionPool, users: list[AuthenticatedUser], admin: psycopg.Connection
) -> None:
    with user_transaction(pool, users[0]) as conn:
        goal = new_goal(conn, users[0], description="Original")
        career_goals.archive_active(conn, users[0].id, goal.id)
        edited = career_goals.update_active(conn, users[0].id, goal.id, {"description": "Tampered"})
        raw = conn.execute(  # even bypassing the repository the database refuses the restore
            "update public.career_goals set status = 'active' where id = %s", [goal.id]
        )

    assert edited is None and raw.rowcount == 0
    assert admin_goal(admin, goal.id) == ("DevOps Engineer", "junior", "Original", "archived")


def test_goals_of_another_user_cannot_be_changed_or_archived(
    pool: ConnectionPool, users: list[AuthenticatedUser], admin: psycopg.Connection
) -> None:
    foreign = admin_create_goal(admin, users[1])

    with user_transaction(pool, users[0]) as conn:
        assert career_goals.update_active(conn, users[0].id, foreign, {"target_role": "X"}) is None
        assert career_goals.update_active(conn, users[1].id, foreign, {"target_role": "X"}) is None
        assert career_goals.archive_active(conn, users[0].id, foreign) is None
        assert career_goals.archive_active(conn, users[1].id, foreign) is None

    assert admin_goal(admin, foreign) == ("Prepared by admin", "senior", None, "active")


# --- Row Level Security as the independent barrier (raw SQL, no repository filters) --------------


def test_rls_alone_hides_and_protects_the_rows_of_other_users(
    pool: ConnectionPool, users: list[AuthenticatedUser], admin: psycopg.Connection
) -> None:
    foreign = admin_create_goal(admin, users[1])
    other = users[1].id

    with user_transaction(pool, users[0]) as conn:
        queries = [
            ("select count(*) from public.career_goals where user_id = %s", [other]),
            ("select count(*) from public.profiles where id = %s", [other]),
        ]
        for query, params in queries:
            assert conn.execute(query, params).fetchone() == (0,)
        goal_update = conn.execute(
            "update public.career_goals set description = 'x' where user_id = %s", [other]
        )
        profile_update = conn.execute(
            "update public.profiles set display_name = 'x' where id = %s", [other]
        )
        assert goal_update.rowcount == 0 and profile_update.rowcount == 0

    assert admin_goal(admin, foreign) == ("Prepared by admin", "senior", None, "active")
    assert admin_profile_name(admin, users[1]) is None


def test_every_user_sees_only_own_rows_in_parallel(users: list[AuthenticatedUser]) -> None:
    pool = open_api_pool(max_size=3)
    barrier = threading.Barrier(len(users))

    def worker(user: AuthenticatedUser) -> None:
        barrier.wait(timeout=10)
        for round_number in range(10):
            with user_transaction(pool, user) as conn:
                profile = profiles.get(conn, user.id)
                assert profile is not None and profile.id == user.id
                goal = career_goals.get_active(conn, user.id)
                if goal is None:
                    goal = new_goal(conn, user, target_role=f"Role of {user.id}")
                assert goal.user_id == user.id and goal.target_role == f"Role of {user.id}"
                updated = profiles.update(
                    conn, user.id, {"display_name": f"{user.id}-{round_number}"}
                )
                assert updated is not None and updated.display_name == f"{user.id}-{round_number}"
                assert conn.execute("select count(*) from public.profiles").fetchone() == (1,)
                assert conn.execute("select count(*) from public.career_goals").fetchone() == (1,)

    try:
        with ThreadPoolExecutor(max_workers=len(users)) as executor:
            for future in [executor.submit(worker, user) for user in users]:
                future.result(timeout=60)
    finally:
        pool.close()


# --- agreement between the Pydantic schemas and the database constraints ------------------------


@pytest.mark.parametrize(
    "target_role",
    ["x", "x" * TARGET_ROLE_MAX_LENGTH, "😀" * TARGET_ROLE_MAX_LENGTH, "Ünïcode ł ó"],
)
@pytest.mark.parametrize("seniority", ["junior", "mid", "senior"])
@pytest.mark.parametrize("description", [None, "y" * GOAL_DESCRIPTION_MAX_LENGTH, "ł" * 2000])
def test_everything_the_goal_schema_accepts_is_accepted_by_the_database(
    pool: ConnectionPool,
    users: list[AuthenticatedUser],
    target_role: str,
    seniority: str,
    description: str | None,
) -> None:
    payload = CareerGoalCreate.model_validate(
        {"target_role": f"  {target_role}\n", "seniority": seniority, "description": description}
    )

    with user_transaction(pool, users[0]) as conn:
        goal = career_goals.insert(
            conn,
            users[0].id,
            target_role=payload.target_role,
            seniority=payload.seniority,
            description=payload.description,
        )

    assert goal.target_role == target_role  # stored stripped
    assert goal.description == description


@pytest.mark.parametrize("language", ["en", "pl"])
@pytest.mark.parametrize(
    "name", ["x", "x" * DISPLAY_NAME_MAX_LENGTH, "😀" * DISPLAY_NAME_MAX_LENGTH]
)
def test_everything_the_profile_schema_accepts_is_accepted_by_the_database(
    pool: ConnectionPool, users: list[AuthenticatedUser], language: str, name: str
) -> None:
    changes = ProfileUpdate.model_validate(
        {"display_name": name, "preferred_language": language}
    ).changes()

    with user_transaction(pool, users[0]) as conn:
        record = profiles.update(conn, users[0].id, changes)

    assert record is not None and (record.display_name, record.preferred_language) == (
        name,
        language,
    )


@pytest.mark.parametrize(
    ("columns", "constraint"),
    [
        ({"target_role": "x" * (TARGET_ROLE_MAX_LENGTH + 1)}, "career_goals_target_role_check"),
        ({"target_role": "   "}, "career_goals_target_role_check"),
        ({"seniority": "lead"}, "career_goals_seniority_check"),
        (
            {"description": "y" * (GOAL_DESCRIPTION_MAX_LENGTH + 1)},
            "career_goals_description_check",
        ),
    ],
)
def test_the_database_still_rejects_what_schema_validation_would_have_stopped_goals(
    pool: ConnectionPool,
    users: list[AuthenticatedUser],
    columns: dict[str, Any],
    constraint: str,
) -> None:
    with pytest.raises(errors.CheckViolation) as exc_info, user_transaction(pool, users[0]) as conn:
        new_goal(conn, users[0], **columns)

    # The mapping of stage 6.4 can rely on the constraint name being reported.
    assert exc_info.value.sqlstate == "23514"
    assert exc_info.value.diag.constraint_name == constraint


@pytest.mark.parametrize(
    ("changes", "constraint"),
    [
        ({"preferred_language": "xx"}, "profiles_preferred_language_check"),
        ({"display_name": ""}, "profiles_display_name_check"),
        ({"display_name": "x" * (DISPLAY_NAME_MAX_LENGTH + 1)}, "profiles_display_name_check"),
    ],
)
def test_the_database_still_rejects_what_schema_validation_would_have_stopped_profiles(
    pool: ConnectionPool,
    users: list[AuthenticatedUser],
    changes: dict[str, Any],
    constraint: str,
) -> None:
    with pytest.raises(errors.CheckViolation) as exc_info, user_transaction(pool, users[0]) as conn:
        profiles.update(conn, users[0].id, changes)

    assert exc_info.value.sqlstate == "23514"
    assert exc_info.value.diag.constraint_name == constraint


def test_not_null_violations_report_the_column_not_a_constraint(
    pool: ConnectionPool, users: list[AuthenticatedUser]
) -> None:
    with (
        pytest.raises(errors.NotNullViolation) as exc_info,
        user_transaction(pool, users[0]) as conn,
    ):
        new_goal(conn, users[0], target_role=None)

    assert exc_info.value.sqlstate == "23502"
    assert exc_info.value.diag.constraint_name is None
    assert exc_info.value.diag.column_name == "target_role"


@pytest.mark.parametrize("field", ["target_role", "description"])
def test_nul_is_refused_by_the_driver_without_a_sqlstate(
    pool: ConnectionPool, users: list[AuthenticatedUser], field: str
) -> None:
    # The schemas reject NUL first. Should it get through, psycopg refuses it on the client
    # side as DataError with NO SQLSTATE, so a mapping by SQLSTATE class alone would miss it
    # (relevant for stage 6.4).
    with pytest.raises(psycopg.DataError) as exc_info, user_transaction(pool, users[0]) as conn:
        new_goal(conn, users[0], **{field: "a\x00b"})

    assert exc_info.value.sqlstate is None
    assert not isinstance(exc_info.value, psycopg.OperationalError)


def test_timestamps_come_back_timezone_aware(
    pool: ConnectionPool, users: list[AuthenticatedUser]
) -> None:
    with user_transaction(pool, users[0]) as conn:
        goal = new_goal(conn, users[0])
        profile = profiles.get(conn, users[0].id)

    assert profile is not None
    for moment in (goal.created_at, goal.updated_at, profile.created_at, profile.updated_at):
        assert moment.utcoffset() is not None
