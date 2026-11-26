"""Unit tests of the competency repository: SQL that is sent and mapping of rows (no database).

The connection double applies the REAL psycopg row factory (`class_row`) to canned rows, so the
mapping from the columns named in the SQL to CompetencyRecord is exercised for real. It also
fails on any use of the connection other than `cursor()`: a repository must not open
transactions, commit, roll back, close or switch connections.
"""

import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from psycopg import errors, pq, sql

from app.models.competency import CompetencyRecord
from app.repositories import competencies

USER_ID = UUID("11111111-1111-1111-1111-111111111111")
GOAL_ID = UUID("22222222-2222-2222-2222-222222222222")
T0 = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)

FIELDS = list(CompetencyRecord.model_fields)  # id, career_goal_id, user_id, ... in table order


def row(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "id": UUID("33333333-3333-3333-3333-333333333333"),
        "career_goal_id": GOAL_ID,
        "user_id": USER_ID,
        "name": "Docker",
        "description": None,
        "rationale": None,
        "category": None,
        "knowledge_type": "application",
        "importance": "high",
        "expected_level": "intermediate",
        "display_order": 0,
        "status": "active",
        "created_at": T0,
        "updated_at": T0,
    }
    values.update(overrides)
    return values


def columns_named_in(statement: str) -> list[str]:
    """The columns a statement returns: the SELECT list or the RETURNING list."""
    if "RETURNING" in statement:
        listing = statement.split("RETURNING", 1)[1]
    else:
        listing = statement.split("SELECT", 1)[1].split(" FROM ", 1)[0]
    return [column.strip() for column in listing.split(",")]


class FakePgResult:
    """The part of a libpq result that psycopg's row factories read."""

    status = pq.ExecStatus.TUPLES_OK

    def __init__(self, names: list[str]) -> None:
        self._names = names
        self.nfields = len(names)

    def fname(self, index: int) -> bytes:
        return self._names[index].encode()


class FakeCursor:
    _encoding = "utf-8"  # read by psycopg's row factories

    def __init__(self, conn: "FakeConnection", row_factory: Callable[[Any], Any]) -> None:
        self._conn = conn
        self._row_factory = row_factory
        self.pgresult: FakePgResult | None = None
        self._maker: Callable[[Any], Any] | None = None
        self._names: list[str] = []
        self._params: Any = None

    def execute(self, query: sql.Composable, params: Any) -> None:
        text = query.as_string()
        self._conn.statements.append((text, params))
        self._params = params
        if self._conn.error is not None:
            raise self._conn.error
        self._names = columns_named_in(text)
        self.pgresult = FakePgResult(self._names)
        self._maker = self._row_factory(self)

    def _map(self, values: dict[str, Any]) -> Any:
        assert self._maker is not None
        return self._maker([values[name] for name in self._names])

    def fetchall(self) -> list[Any]:
        rows = self._conn.rows
        if self._conn.apply_owner_filter:
            # What `WHERE career_goal_id = %s AND user_id = %s` does with the two parameters in
            # that order. It is only as trustworthy as the exact-statement test below.
            goal_id, user_id = self._params
            rows = [r for r in rows if r["career_goal_id"] == goal_id and r["user_id"] == user_id]
        return [self._map(values) for values in rows]

    def fetchone(self) -> Any:
        return self._map(self._conn.rows[0]) if self._conn.rows else None


class FakeConnection:
    """Answers the canned rows. Any attribute but `cursor` is a transaction or connection
    operation that a repository must not use."""

    def __init__(
        self, rows: list[dict[str, Any]] | None = None, *, apply_owner_filter: bool = False
    ) -> None:
        self.rows = rows or []
        self.apply_owner_filter = apply_owner_filter
        self.statements: list[tuple[str, Any]] = []
        self.error: BaseException | None = None

    @contextmanager
    def cursor(self, *, row_factory: Callable[[Any], Any]) -> Iterator[FakeCursor]:
        yield FakeCursor(self, row_factory)

    def __getattr__(self, name: str) -> Any:  # only called for attributes that do not exist
        raise AssertionError(f"a repository must not use conn.{name}")


def list_goal(
    conn: FakeConnection, user_id: UUID = USER_ID, goal_id: UUID = GOAL_ID
) -> list[CompetencyRecord]:
    return competencies.list_by_goal(conn, user_id, goal_id)  # type: ignore[arg-type]


def insert(conn: FakeConnection, **overrides: Any) -> CompetencyRecord:
    values: dict[str, Any] = {
        "name": "Terraform",
        "description": "IaC.",
        "rationale": "Infrastructure is code.",
        "category": "IaC",
        "knowledge_type": "application",
        "importance": "high",
        "expected_level": "intermediate",
        "display_order": 4,
    }
    values.update(overrides)
    return competencies.insert(conn, USER_ID, GOAL_ID, **values)  # type: ignore[arg-type]


# --- list_by_goal ------------------------------------------------------------------------------


def test_rows_are_mapped_to_competency_records() -> None:
    conn = FakeConnection([row(description="D", rationale="R", category="C", display_order=3)])

    (record,) = list_goal(conn)

    assert isinstance(record, CompetencyRecord)
    assert record == CompetencyRecord(
        **row(description="D", rationale="R", category="C", display_order=3)
    )
    assert record.user_id == USER_ID  # the record carries the owner; the API schema does not


def test_active_and_archived_competencies_are_both_listed_in_the_order_returned() -> None:
    conn = FakeConnection(
        [
            row(name="Docker", status="active"),
            row(name="Git", status="archived", display_order=1),
            row(name="Linux", status="active", display_order=2),
        ]
    )

    records = list_goal(conn)

    assert [(r.name, r.status) for r in records] == [
        ("Docker", "active"),
        ("Git", "archived"),
        ("Linux", "active"),
    ]
    assert "status" not in conn.statements[0][0].split("ORDER BY")[0].split("WHERE")[1]


def test_a_goal_without_visible_competencies_gives_an_empty_list() -> None:
    assert list_goal(FakeConnection([])) == []


def test_the_list_query_is_parameterised_and_has_a_total_deterministic_order() -> None:
    conn = FakeConnection([row()])

    list_goal(conn)

    ((statement, params),) = conn.statements
    assert statement == (
        f"SELECT {', '.join(FIELDS)} FROM public.competencies "
        "WHERE career_goal_id = %s AND user_id = %s ORDER BY display_order, created_at, id"
    )
    assert params == [GOAL_ID, USER_ID]


def test_the_where_clause_has_both_the_goal_and_the_owner_condition() -> None:
    conn = FakeConnection([row()])

    list_goal(conn)

    where = conn.statements[0][0].split(" WHERE ", 1)[1].split(" ORDER BY ", 1)[0]
    assert where.split(" AND ") == ["career_goal_id = %s", "user_id = %s"]


def test_the_parameters_follow_the_order_of_the_placeholders() -> None:
    other_user = UUID("44444444-4444-4444-4444-444444444444")
    other_goal = UUID("55555555-5555-5555-5555-555555555555")
    conn = FakeConnection([row()])

    list_goal(conn, other_user, other_goal)

    statement, params = conn.statements[0]
    assert re.findall(r"(\w+) = %s", statement) == ["career_goal_id", "user_id"]
    assert params == [other_goal, other_user]  # goal first, then the owner, as in the SQL
    assert len(params) == statement.count("%s")


def test_the_competencies_of_another_user_are_not_returned() -> None:
    mine = row(name="Mine")
    theirs = row(
        id=UUID("66666666-6666-6666-6666-666666666666"),
        user_id=UUID("44444444-4444-4444-4444-444444444444"),
        name="Theirs",
    )
    conn = FakeConnection([mine, theirs], apply_owner_filter=True)

    assert [r.name for r in list_goal(conn)] == ["Mine"]
    assert [r.name for r in list_goal(conn, user_id=theirs["user_id"])] == ["Theirs"]
    assert list_goal(conn, user_id=UUID("77777777-7777-7777-7777-777777777777")) == []


def test_the_competencies_of_another_goal_are_not_returned() -> None:
    other_goal = UUID("55555555-5555-5555-5555-555555555555")
    conn = FakeConnection(
        [row(name="Here"), row(career_goal_id=other_goal, name="There")], apply_owner_filter=True
    )

    assert [r.name for r in list_goal(conn)] == ["Here"]
    assert [r.name for r in list_goal(conn, goal_id=other_goal)] == ["There"]


def test_the_list_selects_exactly_the_fields_of_the_record_in_table_order() -> None:
    conn = FakeConnection([row()])

    list_goal(conn)

    assert columns_named_in(conn.statements[0][0]) == FIELDS


# --- insert ------------------------------------------------------------------------------------


def test_the_inserted_record_is_returned_as_the_database_gave_it() -> None:
    created = row(
        name="Terraform",
        description="IaC.",
        rationale="Infrastructure is code.",
        category="IaC",
        display_order=4,
    )
    conn = FakeConnection([created])

    record = insert(conn)

    assert record == CompetencyRecord(**created)
    assert record.status == "active"


def test_insert_sends_one_parameterised_statement_with_returning() -> None:
    conn = FakeConnection([row()])

    insert(conn)

    ((statement, params),) = conn.statements
    assert statement == (
        "INSERT INTO public.competencies (career_goal_id, user_id, name, description, rationale, "
        "category, knowledge_type, importance, expected_level, display_order) "
        f"VALUES ({', '.join(['%s'] * 10)}) RETURNING {', '.join(FIELDS)}"
    )
    assert params == [
        GOAL_ID,
        USER_ID,
        "Terraform",
        "IaC.",
        "Infrastructure is code.",
        "IaC",
        "application",
        "high",
        "intermediate",
        4,
    ]
    assert columns_named_in(statement) == FIELDS


def test_insert_never_sends_a_status_an_id_or_timestamps() -> None:
    conn = FakeConnection([row()])

    insert(conn)

    column_list = conn.statements[0][0].split("VALUES")[0]
    for column in ("status", " id", "created_at", "updated_at"):
        assert column not in column_list


def test_insert_passes_none_for_the_optional_fields() -> None:
    conn = FakeConnection([row()])

    insert(conn, description=None, rationale=None, category=None)

    assert conn.statements[0][1][3:6] == [None, None, None]


def test_values_are_never_interpolated_into_the_sql() -> None:
    hostile = "x'); DROP TABLE public.competencies; --"
    conn = FakeConnection([row()])

    insert(conn, name=hostile, description=hostile, rationale=hostile, category=hostile)

    statement, params = conn.statements[0]
    assert "DROP TABLE" not in statement and "'" not in statement
    assert params.count(hostile) == 4
    assert statement.count("%s") == len(params) == 10


def test_the_owner_comes_from_the_caller_and_the_goal_from_the_path_in_that_column_order() -> None:
    other_user = UUID("44444444-4444-4444-4444-444444444444")
    other_goal = UUID("55555555-5555-5555-5555-555555555555")
    conn = FakeConnection([row()])

    competencies.insert(  # type: ignore[arg-type]
        conn,
        other_user,
        other_goal,
        name="n",
        description=None,
        rationale=None,
        category=None,
        knowledge_type="memory",
        importance="low",
        expected_level="beginner",
        display_order=0,
    )

    assert conn.statements[0][1][:2] == [other_goal, other_user]  # career_goal_id, user_id


def test_insert_fails_loudly_if_the_database_returns_no_row() -> None:
    with pytest.raises(RuntimeError, match="no row"):
        insert(FakeConnection([]))


def test_insert_takes_the_fields_by_keyword_only() -> None:
    with pytest.raises(TypeError):
        competencies.insert(FakeConnection(), USER_ID, GOAL_ID, "name")  # type: ignore[call-arg, arg-type]


# --- what the repository does not do -----------------------------------------------------------


def test_insert_makes_no_checks_before_writing() -> None:
    """Goal status, the limit of 50 and the duplicate name are enforced by the database."""
    conn = FakeConnection([row()])

    insert(conn)

    assert len(conn.statements) == 1
    assert re.match(r"INSERT INTO public\.competencies", conn.statements[0][0])
    assert not re.search(r"count\(|exists|SELECT .* FROM", conn.statements[0][0], re.IGNORECASE)


@pytest.mark.parametrize(
    "error",
    [
        errors.UniqueViolation("duplicate"),
        errors.CheckViolation("limit"),
        errors.InsufficientPrivilege("denied"),
        errors.DeadlockDetected("deadlock"),
    ],
    ids=lambda e: type(e).__name__,
)
@pytest.mark.parametrize("call", [list_goal, insert], ids=["list_by_goal", "insert"])
def test_database_errors_are_not_caught_or_translated(
    call: Callable[[FakeConnection], Any], error: BaseException
) -> None:
    conn = FakeConnection([row()])
    conn.error = error

    with pytest.raises(type(error)) as exc_info:
        call(conn)

    assert exc_info.value is error
    assert len(conn.statements) == 1  # no retry, no fallback statement


@pytest.mark.parametrize("call", [list_goal, insert], ids=["list_by_goal", "insert"])
def test_the_repository_only_asks_the_connection_for_a_cursor(
    call: Callable[[FakeConnection], Any],
) -> None:
    # FakeConnection raises AssertionError on transaction(), commit(), rollback(), close() ...
    call(FakeConnection([row()]))


# --- get_by_id, update_active, archive_active ---------------------------------------------------

COMPETENCY_ID = UUID("33333333-3333-3333-3333-333333333333")


def get_one(conn: FakeConnection) -> CompetencyRecord | None:
    return competencies.get_by_id(conn, USER_ID, COMPETENCY_ID)  # type: ignore[arg-type]


def update(conn: FakeConnection, **changes: Any) -> CompetencyRecord | None:
    return competencies.update_active(
        conn,
        USER_ID,
        COMPETENCY_ID,
        changes or {"name": "Git"},  # type: ignore[arg-type]
    )


def archive(conn: FakeConnection) -> CompetencyRecord | None:
    return competencies.archive_active(conn, USER_ID, COMPETENCY_ID)  # type: ignore[arg-type]


def test_get_by_id_is_scoped_to_the_owner_and_returns_any_status() -> None:
    conn = FakeConnection([row(status="archived")])

    record = get_one(conn)

    assert record is not None and record.status == "archived"
    ((statement, params),) = conn.statements
    assert statement == (
        f"SELECT {', '.join(FIELDS)} FROM public.competencies WHERE id = %s AND user_id = %s"
    )
    assert params == [COMPETENCY_ID, USER_ID]


def test_update_sets_only_the_given_columns_of_an_active_competency_of_the_user() -> None:
    conn = FakeConnection([row(name="Git", category=None)])

    record = update(conn, name="Git", category=None)

    assert record is not None and record.name == "Git"
    ((statement, params),) = conn.statements
    assert statement == (
        'UPDATE public.competencies SET "name" = %s, "category" = %s '
        f"WHERE id = %s AND user_id = %s AND status = 'active' RETURNING {', '.join(FIELDS)}"
    )
    assert params == ["Git", None, COMPETENCY_ID, USER_ID]


@pytest.mark.parametrize(
    "column", ["status", "id", "user_id", "career_goal_id", "created_at", "updated_at"]
)
def test_update_refuses_a_column_that_is_not_updatable(column: str) -> None:
    conn = FakeConnection([row()])

    with pytest.raises(ValueError, match="cannot be updated"):
        update(conn, **{column: "x"})

    assert conn.statements == []


def test_archive_only_sets_the_status_of_an_active_competency_of_the_user() -> None:
    conn = FakeConnection([row(status="archived")])

    record = archive(conn)

    assert record is not None and record.status == "archived"
    ((statement, params),) = conn.statements
    assert statement == (
        "UPDATE public.competencies SET status = 'archived' "
        f"WHERE id = %s AND user_id = %s AND status = 'active' RETURNING {', '.join(FIELDS)}"
    )
    assert params == [COMPETENCY_ID, USER_ID]


@pytest.mark.parametrize("call", [get_one, update, archive], ids=["get", "update", "archive"])
def test_no_matching_row_gives_none_after_one_statement(
    call: Callable[[FakeConnection], Any],
) -> None:
    conn = FakeConnection([])

    assert call(conn) is None
    assert len(conn.statements) == 1


@pytest.mark.parametrize("call", [get_one, update, archive], ids=["get", "update", "archive"])
def test_database_errors_of_the_item_statements_are_not_caught(
    call: Callable[[FakeConnection], Any],
) -> None:
    conn = FakeConnection([row()])
    conn.error = errors.CheckViolation("refused")

    with pytest.raises(errors.CheckViolation):
        call(conn)
