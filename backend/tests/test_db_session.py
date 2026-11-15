"""Unit tests of user_transaction() with doubles: no database, no network."""

import json
import threading
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg import errors

from app.db.errors import DatabaseUnavailableError
from app.db.session import (
    NestedUserTransactionError,
    UserContextMismatchError,
    user_transaction,
)
from app.models.auth import AuthenticatedUser
from tests.db_fakes import FakeUserConnection, FakeUserPool

USER = AuthenticatedUser(id=UUID("11111111-1111-1111-1111-111111111111"))
OTHER = AuthenticatedUser(id=UUID("22222222-2222-2222-2222-222222222222"))


def make_pool(**kwargs: object) -> FakeUserPool:
    events: list[str] = []
    return FakeUserPool(FakeUserConnection(events, uid=USER.id, **kwargs))  # type: ignore[arg-type]


def test_successful_transaction_follows_the_agreed_order() -> None:
    pool = make_pool()

    with user_transaction(pool, USER) as conn:  # type: ignore[arg-type]
        pool.events.append("block")
        assert conn is pool.conn

    assert pool.events == [
        "pool.acquire",
        "BEGIN",
        "SET LOCAL ROLE authenticated",
        "SELECT set_config",
        "select auth.uid(), current_user",
        "block",
        "COMMIT",
        "pool.release",
    ]


def test_claims_contain_only_sub_and_role_from_the_verified_user() -> None:
    pool = make_pool()

    with user_transaction(pool, USER):  # type: ignore[arg-type]
        pass

    (claims_param,) = pool.conn.params[0]
    assert json.loads(claims_param) == {"sub": str(USER.id), "role": "authenticated"}


def test_role_and_claims_are_transaction_local() -> None:
    pool = make_pool()

    with user_transaction(pool, USER):  # type: ignore[arg-type]
        pass

    role_sql, claims_sql, _ = pool.conn.sqls
    assert role_sql == "SET LOCAL ROLE authenticated"  # never a session-level SET ROLE
    assert claims_sql.startswith("SELECT set_config('request.jwt.claims', %s, true)")
    assert "claim.sub" not in claims_sql  # the legacy session setting is never written


def test_exception_in_the_block_rolls_back_and_propagates() -> None:
    pool = make_pool()

    with pytest.raises(ValueError, match="boom"), user_transaction(pool, USER):  # type: ignore[arg-type]
        raise ValueError("boom")

    assert "ROLLBACK" in pool.events and "COMMIT" not in pool.events
    assert pool.events[-1] == "pool.release"


@pytest.mark.parametrize("bad", [str(USER.id), USER.id, {"id": str(USER.id)}, None])
def test_only_an_authenticated_user_object_is_accepted(bad: object) -> None:
    pool = make_pool()

    with pytest.raises(TypeError), user_transaction(pool, bad):  # type: ignore[arg-type]
        pytest.fail("block must not run")

    assert pool.events == []  # no connection was even taken


@pytest.mark.parametrize(
    "state",
    [{"uid": OTHER.id}, {"uid": None}, {"uid": USER.id, "role": "skillloop_api"}],
)
def test_identity_mismatch_aborts_before_the_block_and_rolls_back(state: dict[str, object]) -> None:
    events: list[str] = []
    pool = FakeUserPool(FakeUserConnection(events, **state))  # type: ignore[arg-type]

    with pytest.raises(UserContextMismatchError) as exc_info, user_transaction(pool, USER):  # type: ignore[arg-type]
        pytest.fail("block must not run")

    assert "ROLLBACK" in events and "COMMIT" not in events
    assert str(USER.id) not in str(exc_info.value) and str(OTHER.id) not in str(exc_info.value)


# --- nesting --------------------------------------------------------------------------------


@pytest.mark.parametrize("inner_user", [USER, OTHER])
def test_nested_call_is_rejected_for_any_user_without_taking_a_second_connection(
    inner_user: AuthenticatedUser,
) -> None:
    pool = make_pool()

    with user_transaction(pool, USER):  # type: ignore[arg-type]
        acquires_before = pool.events.count("pool.acquire")
        with pytest.raises(NestedUserTransactionError), user_transaction(pool, inner_user):  # type: ignore[arg-type]
            pytest.fail("block must not run")
        assert pool.events.count("pool.acquire") == acquires_before

    assert pool.events[-1] == "pool.release"
    assert "COMMIT" in pool.events  # the outer transaction was not harmed


def test_guard_is_released_after_normal_exit_and_after_a_block_error() -> None:
    pool = make_pool()

    with user_transaction(pool, USER):  # type: ignore[arg-type]
        pass
    with pytest.raises(ValueError), user_transaction(pool, USER):  # type: ignore[arg-type]
        raise ValueError
    with user_transaction(pool, USER):  # type: ignore[arg-type]
        pass  # would raise NestedUserTransactionError if the guard had stuck


@pytest.mark.parametrize("fail_on", ["begin", "set_role"])
def test_guard_is_released_when_setup_fails_and_errors_are_translated(fail_on: str) -> None:
    events: list[str] = []
    pool = FakeUserPool(FakeUserConnection(events, uid=USER.id, fail_on=fail_on))

    with pytest.raises(DatabaseUnavailableError), user_transaction(pool, USER):  # type: ignore[arg-type]
        pytest.fail("block must not run")

    with user_transaction(make_pool(), USER):  # type: ignore[arg-type]
        pass


def test_guard_is_released_when_no_connection_can_be_acquired() -> None:
    pool = FakeUserPool(fail_acquire=True)

    with pytest.raises(DatabaseUnavailableError), user_transaction(pool, USER):  # type: ignore[arg-type]
        pytest.fail("block must not run")

    with user_transaction(make_pool(), USER):  # type: ignore[arg-type]
        pass


def test_guard_is_per_thread() -> None:
    pool_a, pool_b = make_pool(), make_pool()
    errors_seen: list[BaseException] = []
    inside = threading.Event()
    release = threading.Event()

    def hold() -> None:
        with user_transaction(pool_a, USER):  # type: ignore[arg-type]
            inside.set()
            release.wait(5)

    thread = threading.Thread(target=hold)
    thread.start()
    assert inside.wait(5)
    try:
        # Another request (thread) is not blocked by the open transaction of this one.
        with user_transaction(pool_b, USER):  # type: ignore[arg-type]
            pass
    except BaseException as exc:  # noqa: BLE001
        errors_seen.append(exc)
    finally:
        release.set()
        thread.join()

    assert errors_seen == []


# --- the whole life cycle is covered by error translation ----------------------------------


def test_commit_failure_is_translated() -> None:
    events: list[str] = []
    pool = FakeUserPool(FakeUserConnection(events, uid=USER.id, fail_on="commit"))

    with pytest.raises(DatabaseUnavailableError), user_transaction(pool, USER):  # type: ignore[arg-type]
        pass

    assert events[-1] == "pool.release"


def test_rollback_failure_is_translated_and_the_connection_is_still_released() -> None:
    events: list[str] = []
    pool = FakeUserPool(FakeUserConnection(events, uid=USER.id, fail_on="rollback"))

    with pytest.raises(DatabaseUnavailableError), user_transaction(pool, USER):  # type: ignore[arg-type]
        raise ValueError("block failed")

    assert events[-1] == "pool.release"


def test_connection_error_raised_by_the_block_is_translated() -> None:
    pool = make_pool()

    with pytest.raises(DatabaseUnavailableError), user_transaction(pool, USER):  # type: ignore[arg-type]
        raise psycopg.OperationalError("connection lost")


def test_sql_errors_are_not_translated() -> None:
    pool = make_pool()

    with pytest.raises(errors.UniqueViolation), user_transaction(pool, USER):  # type: ignore[arg-type]
        raise errors.UniqueViolation("duplicate key")


def test_different_users_get_their_own_claims() -> None:
    subs = set()
    for _ in range(5):
        user = AuthenticatedUser(id=uuid4())
        pool = FakeUserPool(FakeUserConnection([], uid=user.id))
        with user_transaction(pool, user):  # type: ignore[arg-type]
            pass
        subs.add(json.loads(pool.conn.params[0][0])["sub"])
    assert len(subs) == 5
