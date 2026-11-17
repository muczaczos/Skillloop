"""Profile and career goal services against a REAL local PostgreSQL / Supavisor.

Real user_transaction(), Row Level Security, repositories and services; nothing is mocked.
Needs TEST_DATABASE_URL and TEST_DATABASE_ADMIN_URL (see db_integration_helpers); the
throwaway users come from the `users` fixture (conftest.py), which deletes them and, by
cascade, everything these tests create. Rows that existed before are never touched.
"""

from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg_pool import ConnectionPool

from app.core.errors import (
    ActiveGoalExistsError,
    GoalArchivedError,
    GoalNotFoundError,
    NoActiveGoalError,
    ProfileNotFoundError,
)
from app.models.auth import AuthenticatedUser
from app.schemas.career_goal import CareerGoalCreate, CareerGoalUpdate
from app.schemas.profile import ProfileUpdate
from app.services import career_goal_service, profile_service
from tests.db_integration_helpers import TEST_DATABASE_ADMIN_URL, TEST_DATABASE_URL

pytestmark = pytest.mark.skipif(
    not (TEST_DATABASE_URL and TEST_DATABASE_ADMIN_URL),
    reason="TEST_DATABASE_URL and TEST_DATABASE_ADMIN_URL are not both set",
)

CREATE = CareerGoalCreate(target_role="Backend Engineer", seniority="mid", description="API")


def admin_goal(admin: psycopg.Connection, goal_id: UUID) -> tuple[Any, ...] | None:
    return admin.execute(
        "select target_role, seniority, description, status from public.career_goals where id = %s",
        [goal_id],
    ).fetchone()


def admin_display_name(admin: psycopg.Connection, user: AuthenticatedUser) -> str | None:
    return admin.execute(  # type: ignore[no-any-return, index]
        "select display_name from public.profiles where id = %s", [user.id]
    ).fetchone()[0]


# --- profile -----------------------------------------------------------------------------


def test_get_profile(pool: ConnectionPool, users: list[AuthenticatedUser]) -> None:
    profile = profile_service.get_profile(pool, users[0])

    assert profile.id == users[0].id
    assert profile.preferred_language in ("en", "pl")


def test_get_profile_of_user_without_profile_raises_not_found(
    pool: ConnectionPool, users: list[AuthenticatedUser], admin: psycopg.Connection
) -> None:
    admin.execute("delete from public.profiles where id = %s", [users[0].id])

    with pytest.raises(ProfileNotFoundError):
        profile_service.get_profile(pool, users[0])
    with pytest.raises(ProfileNotFoundError):
        profile_service.update_profile(pool, users[0], ProfileUpdate(display_name="x"))


def test_update_profile(
    pool: ConnectionPool, users: list[AuthenticatedUser], admin: psycopg.Connection
) -> None:
    updated = profile_service.update_profile(
        pool, users[0], ProfileUpdate(display_name="Ada", preferred_language="pl")
    )

    assert (updated.display_name, updated.preferred_language) == ("Ada", "pl")
    assert profile_service.get_profile(pool, users[0]) == updated
    assert admin_display_name(admin, users[0]) == "Ada"


def test_update_profile_keeps_omitted_fields_and_applies_explicit_null(
    pool: ConnectionPool, users: list[AuthenticatedUser]
) -> None:
    profile_service.update_profile(
        pool, users[0], ProfileUpdate(display_name="Ada", preferred_language="pl")
    )

    only_language = profile_service.update_profile(
        pool, users[0], ProfileUpdate(preferred_language="en")
    )
    assert (only_language.display_name, only_language.preferred_language) == ("Ada", "en")

    cleared = profile_service.update_profile(pool, users[0], ProfileUpdate(display_name=None))
    assert (cleared.display_name, cleared.preferred_language) == (None, "en")


def test_profiles_of_other_users_are_untouched(
    pool: ConnectionPool, users: list[AuthenticatedUser], admin: psycopg.Connection
) -> None:
    profile_service.update_profile(pool, users[1], ProfileUpdate(display_name="Grace"))

    mine = profile_service.update_profile(pool, users[0], ProfileUpdate(display_name="Ada"))

    # The service always acts on the token's user: there is no way to name another profile.
    assert mine.id == users[0].id
    assert profile_service.get_profile(pool, users[1]).display_name == "Grace"
    assert admin_display_name(admin, users[1]) == "Grace"


# --- career goals ------------------------------------------------------------------------


def test_create_and_get_active_goal(pool: ConnectionPool, users: list[AuthenticatedUser]) -> None:
    created = career_goal_service.create_goal(pool, users[0], CREATE)

    assert created.user_id == users[0].id
    assert (created.target_role, created.seniority, created.description, created.status) == (
        "Backend Engineer",
        "mid",
        "API",
        "active",
    )
    assert career_goal_service.get_active_goal(pool, users[0]) == created


def test_no_active_goal(pool: ConnectionPool, users: list[AuthenticatedUser]) -> None:
    with pytest.raises(NoActiveGoalError):
        career_goal_service.get_active_goal(pool, users[0])


def test_second_active_goal_is_refused_and_leaves_the_first_intact(
    pool: ConnectionPool, users: list[AuthenticatedUser]
) -> None:
    first = career_goal_service.create_goal(pool, users[0], CREATE)

    with pytest.raises(ActiveGoalExistsError):
        career_goal_service.create_goal(pool, users[0], CREATE)

    assert career_goal_service.get_active_goal(pool, users[0]) == first
    # The pooled connection is healthy after the failed transaction.
    assert profile_service.get_profile(pool, users[0]).id == users[0].id


def test_update_goal(
    pool: ConnectionPool, users: list[AuthenticatedUser], admin: psycopg.Connection
) -> None:
    goal = career_goal_service.create_goal(pool, users[0], CREATE)

    updated = career_goal_service.update_goal(
        pool, users[0], goal.id, CareerGoalUpdate(seniority="senior", description=None)
    )

    assert (updated.target_role, updated.seniority, updated.description) == (
        "Backend Engineer",
        "senior",
        None,
    )
    assert admin_goal(admin, goal.id) == ("Backend Engineer", "senior", None, "active")


def test_archive_goal_then_no_active_goal_and_a_new_one_is_allowed(
    pool: ConnectionPool, users: list[AuthenticatedUser]
) -> None:
    goal = career_goal_service.create_goal(pool, users[0], CREATE)

    archived = career_goal_service.archive_goal(pool, users[0], goal.id)

    assert archived.id == goal.id and archived.status == "archived"
    with pytest.raises(NoActiveGoalError):
        career_goal_service.get_active_goal(pool, users[0])
    new = career_goal_service.create_goal(pool, users[0], CREATE)
    assert new.id != goal.id and new.status == "active"


def test_archived_goal_is_classified_and_immutable(
    pool: ConnectionPool, users: list[AuthenticatedUser], admin: psycopg.Connection
) -> None:
    goal = career_goal_service.create_goal(pool, users[0], CREATE)
    career_goal_service.archive_goal(pool, users[0], goal.id)

    with pytest.raises(GoalArchivedError):
        career_goal_service.update_goal(
            pool, users[0], goal.id, CareerGoalUpdate(seniority="junior")
        )
    with pytest.raises(GoalArchivedError):
        career_goal_service.archive_goal(pool, users[0], goal.id)

    assert admin_goal(admin, goal.id) == ("Backend Engineer", "mid", "API", "archived")


def test_unknown_goal_id_raises_not_found(
    pool: ConnectionPool, users: list[AuthenticatedUser]
) -> None:
    with pytest.raises(GoalNotFoundError):
        career_goal_service.update_goal(
            pool, users[0], uuid4(), CareerGoalUpdate(seniority="junior")
        )
    with pytest.raises(GoalNotFoundError):
        career_goal_service.archive_goal(pool, users[0], uuid4())


def test_goal_of_another_user_is_invisible_and_untouched(
    pool: ConnectionPool, users: list[AuthenticatedUser], admin: psycopg.Connection
) -> None:
    theirs = career_goal_service.create_goal(pool, users[1], CREATE)

    # Neither the active goal nor the id of someone else's goal is reachable.
    with pytest.raises(NoActiveGoalError):
        career_goal_service.get_active_goal(pool, users[0])
    with pytest.raises(GoalNotFoundError):
        career_goal_service.update_goal(
            pool, users[0], theirs.id, CareerGoalUpdate(seniority="junior")
        )
    with pytest.raises(GoalNotFoundError):
        career_goal_service.archive_goal(pool, users[0], theirs.id)

    assert admin_goal(admin, theirs.id) == ("Backend Engineer", "mid", "API", "active")
    # Having no goal of my own, I can still create one while theirs is active.
    mine = career_goal_service.create_goal(pool, users[0], CREATE)
    assert mine.user_id == users[0].id
    assert career_goal_service.get_active_goal(pool, users[1]) == theirs
