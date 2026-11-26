"""Unit tests of the repositories: SQL composition and architecture rules (no database)."""

import ast
import re
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from psycopg import sql

from app.repositories import _sql, career_goals, competencies, profiles

USER_ID = UUID("11111111-1111-1111-1111-111111111111")
GOAL_ID = UUID("22222222-2222-2222-2222-222222222222")
REPOSITORY_DIR = Path(__file__).resolve().parents[1] / "app" / "repositories"


class RecordingConnection:
    """Captures what a repository sends; returns no row."""

    def __init__(self) -> None:
        self.statements: list[tuple[str, list[Any]]] = []

    @contextmanager
    def cursor(self, **_: Any) -> Iterator["RecordingConnection"]:
        yield self

    def execute(self, query: sql.Composable, params: list[Any]) -> None:
        self.statements.append((query.as_string(), params))

    def fetchone(self) -> None:
        return None


# --- set_clause ------------------------------------------------------------------------------


def test_set_clause_quotes_identifiers_and_keeps_the_order_of_the_changes() -> None:
    clause, values = _sql.set_clause(
        {"preferred_language": "pl", "display_name": None}, profiles.UPDATABLE_COLUMNS
    )

    assert clause.as_string() == '"preferred_language" = %s, "display_name" = %s'
    assert values == ["pl", None]


@pytest.mark.parametrize(
    "column",
    [
        "id",
        "user_id",
        "status",
        "created_at",
        "updated_at",
        "display_name; DROP TABLE public.profiles",
        'display_name" = %s, "id',
        "",
    ],
)
def test_set_clause_rejects_every_column_outside_the_allow_list(column: str) -> None:
    with pytest.raises(ValueError, match="cannot be updated"):
        _sql.set_clause({"display_name": "x", column: "y"}, profiles.UPDATABLE_COLUMNS)


def test_set_clause_rejects_an_empty_change_set() -> None:
    with pytest.raises(ValueError, match="nothing to update"):
        _sql.set_clause({}, profiles.UPDATABLE_COLUMNS)


def test_allow_lists_match_the_column_grants_of_the_migrations() -> None:
    assert profiles.UPDATABLE_COLUMNS == ("display_name", "preferred_language")
    # `status` is deliberately absent: a goal is archived only through archive_active().
    assert career_goals.UPDATABLE_COLUMNS == ("target_role", "seniority", "description")
    assert competencies.UPDATABLE_COLUMNS == (
        "name",
        "description",
        "rationale",
        "category",
        "knowledge_type",
        "importance",
        "expected_level",
        "display_order",
    )


# --- the SQL that is sent --------------------------------------------------------------------


def test_profile_update_sends_a_parameterised_statement_scoped_to_the_user() -> None:
    conn = RecordingConnection()

    assert profiles.update(conn, USER_ID, {"display_name": "Ann"}) is None  # type: ignore[arg-type]

    ((statement, params),) = conn.statements
    assert statement.startswith('UPDATE public.profiles SET "display_name" = %s WHERE id = %s')
    assert " RETURNING id, display_name, preferred_language, created_at, updated_at" in statement
    assert params == ["Ann", USER_ID]


def test_goal_update_is_limited_to_active_goals_of_the_user() -> None:
    conn = RecordingConnection()

    assert career_goals.update_active(conn, USER_ID, GOAL_ID, {"description": None}) is None  # type: ignore[arg-type]

    ((statement, params),) = conn.statements
    assert "WHERE id = %s AND user_id = %s AND status = 'active' RETURNING " in statement
    assert params == [None, GOAL_ID, USER_ID]


def test_archive_only_touches_active_goals_of_the_user() -> None:
    conn = RecordingConnection()

    assert career_goals.archive_active(conn, USER_ID, GOAL_ID) is None  # type: ignore[arg-type]

    ((statement, params),) = conn.statements
    assert statement.startswith("UPDATE public.career_goals")
    assert "SET status = 'archived'" in statement
    assert "WHERE id = %s AND user_id = %s AND status = 'active'" in statement
    assert params == [GOAL_ID, USER_ID]


def test_reads_are_scoped_to_the_user() -> None:
    conn = RecordingConnection()

    profiles.get(conn, USER_ID)  # type: ignore[arg-type]
    career_goals.get_by_id(conn, USER_ID, GOAL_ID)  # type: ignore[arg-type]
    career_goals.get_active(conn, USER_ID)  # type: ignore[arg-type]

    (profile, _), (by_id, by_id_params), (active, _) = conn.statements
    assert profile.endswith("FROM public.profiles WHERE id = %s")
    assert by_id.endswith("FROM public.career_goals WHERE id = %s AND user_id = %s")
    assert by_id_params == [GOAL_ID, USER_ID]
    assert active.endswith("WHERE user_id = %s AND status = 'active'")


def test_insert_never_sends_a_status() -> None:
    class OneRowConnection(RecordingConnection):
        def fetchone(self) -> Any:
            return object()  # any row: only the statement matters here

    conn = OneRowConnection()

    career_goals.insert(  # type: ignore[arg-type]
        conn, USER_ID, target_role="DevOps", seniority="mid", description=None
    )

    ((statement, params),) = conn.statements
    assert "(user_id, target_role, seniority, description) VALUES (%s, %s, %s, %s)" in statement
    assert params == [USER_ID, "DevOps", "mid", None]


@pytest.mark.parametrize("bad", ["id", "user_id", "status", "created_at"])
def test_updates_with_a_forbidden_column_never_reach_the_database(bad: str) -> None:
    conn = RecordingConnection()

    with pytest.raises(ValueError):
        profiles.update(conn, USER_ID, {bad: "x"})  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        career_goals.update_active(conn, USER_ID, GOAL_ID, {bad: "x"})  # type: ignore[arg-type]

    assert conn.statements == []


# --- architecture rules ------------------------------------------------------------------------


def _modules() -> list[Path]:
    modules = sorted(p for p in REPOSITORY_DIR.glob("*.py") if p.name != "__init__.py")
    assert {p.name for p in modules} == {
        "_sql.py",
        "career_goals.py",
        "competencies.py",
        "profiles.py",
    }
    return modules


REPOSITORY_PACKAGE = "app.repositories"
FORBIDDEN_IMPORT_PREFIXES = ("app.db", "psycopg_pool")


def _imported_names(source: str, package: str) -> set[str]:
    """Fully qualified names of everything a module imports, as dotted paths.

    `import a.b`          -> a.b
    `from a.b import c`   -> a.b and a.b.c   (c may be a module or an object)
    `from . import c`     -> resolved against `package` (relative imports included)
    """
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:  # relative: strip `level - 1` trailing parts of the package
                anchor = package.split(".")[: len(package.split(".")) - (node.level - 1)]
                base = ".".join([*anchor, *([node.module] if node.module else [])])
            names.add(base)
            names.update(f"{base}.{alias.name}" for alias in node.names)
    return names


def _forbidden_imports(source: str, package: str) -> list[str]:
    return sorted(
        name
        for name in _imported_names(source, package)
        for prefix in FORBIDDEN_IMPORT_PREFIXES
        if name == prefix or name.startswith(prefix + ".")
    )


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("import app.db.pool", ["app.db.pool"]),
        ("import psycopg_pool", ["psycopg_pool"]),
        ("import app.db.session as s", ["app.db.session"]),
        ("from app.db import pool", ["app.db", "app.db.pool"]),
        (
            "from app.db.session import user_transaction",
            ["app.db.session", "app.db.session.user_transaction"],
        ),
        (
            "from psycopg_pool import ConnectionPool",
            ["psycopg_pool", "psycopg_pool.ConnectionPool"],
        ),
        ("from ..db import pool", ["app.db", "app.db.pool"]),
        ("from ..db.pool import create_pool", ["app.db.pool", "app.db.pool.create_pool"]),
        ("from .. import db", ["app.db"]),
    ],
)
def test_the_import_check_detects_every_way_to_reach_the_database_layer(
    source: str, expected: list[str]
) -> None:
    assert _forbidden_imports(source, package=REPOSITORY_PACKAGE) == expected


@pytest.mark.parametrize(
    "source",
    [
        "import psycopg",
        "from psycopg import Connection, sql",
        "from psycopg.rows import class_row",
        "from app.models.profile import ProfileRecord",
        "from app.repositories import _sql",
        "from ..models import profile",
        "from . import _sql",
        "import app.database_utils",  # a different package that merely starts with "app.db"
    ],
)
def test_the_import_check_allows_what_a_repository_may_use(source: str) -> None:
    assert _forbidden_imports(source, package=REPOSITORY_PACKAGE) == []


def _string_literals(tree: ast.Module) -> list[str]:
    """All string constants except docstrings (those are SQL text and messages)."""
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Module | ast.FunctionDef | ast.ClassDef)
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
    }
    return [
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docstrings
    ]


@pytest.mark.parametrize("module", _modules(), ids=lambda p: p.name)
def test_repositories_do_not_manage_transactions_connections_or_roles(module: Path) -> None:
    tree = ast.parse(module.read_text())

    called = {
        n.func.attr
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
    }
    assert not called & {"transaction", "commit", "rollback", "set_autocommit", "connection"}

    assert not _forbidden_imports(module.read_text(), package=REPOSITORY_PACKAGE)

    forbidden = re.compile(
        r"SELECT\s+\*|\bSET\s+(LOCAL\s+|SESSION\s+)?ROLE\b|SET_CONFIG|\bBEGIN\b|\bCOMMIT\b"
        r"|\bROLLBACK\b|\bRESET\b|\bSET\s+LOCAL\b",
        re.IGNORECASE,
    )
    for literal in _string_literals(tree):
        assert not forbidden.search(literal), literal


@pytest.mark.parametrize("module", ["profiles.py", "career_goals.py", "competencies.py"])
def test_sql_is_never_built_with_f_strings_or_concatenation(module: str) -> None:
    tree = ast.parse((REPOSITORY_DIR / module).read_text())

    assert not [n for n in ast.walk(tree) if isinstance(n, ast.JoinedStr)], "f-string found"
    assert not [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.BinOp)
        and isinstance(n.op, ast.Mod | ast.Add)
        and any(
            isinstance(side, ast.Constant) and isinstance(side.value, str)
            for side in (n.left, n.right)
        )
    ], "string formatting or concatenation found"
