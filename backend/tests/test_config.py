import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_supabase_urls_are_derived_from_project_url() -> None:
    settings = Settings(supabase_url="https://abc.supabase.co/")

    assert settings.supabase_url == "https://abc.supabase.co"
    assert settings.supabase_jwt_issuer == "https://abc.supabase.co/auth/v1"
    assert settings.supabase_jwks_url == "https://abc.supabase.co/auth/v1/.well-known/jwks.json"
    assert settings.supabase_jwt_audience == "authenticated"


def test_supabase_url_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SUPABASE_URL", raising=False)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_supabase_url_must_be_http_url() -> None:
    with pytest.raises(ValidationError):
        Settings(supabase_url="abc.supabase.co")


# --- Database settings -------------------------------------------------------------------

VALID_DB_URL = "postgresql://skillloop_api.abcd1234:s3cr3t-pw@pooler.example.com:5432/postgres"


def make_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "supabase_url": "https://abc.supabase.co",
        "database_url": VALID_DB_URL,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


def test_database_url_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None, supabase_url="https://abc.supabase.co")

    assert "database_url" in str(exc_info.value)


def test_database_url_is_a_secret() -> None:
    settings = make_settings()

    assert settings.database_url.get_secret_value() == VALID_DB_URL
    for text in (
        repr(settings),
        str(settings),
        repr(settings.database_url),
        str(settings.database_url),
    ):
        assert "s3cr3t-pw" not in text
        assert "pooler.example.com" not in text


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://skillloop_api:pw@localhost:5432/postgres",
        "postgres://skillloop_api.abcd1234:pw@localhost:5432/postgres",
        "postgresql://skillloop_api.pooler-dev:pw@127.0.0.1:54329/postgres",
    ],
)
def test_database_url_accepts_the_api_role(url: str) -> None:
    assert make_settings(database_url=url).database_url.get_secret_value() == url


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://postgres:pw-leak-check@localhost:5432/postgres",
        "postgresql://postgres.abcd1234:pw-leak-check@localhost:5432/postgres",
        "postgresql://service_role:pw-leak-check@localhost:5432/postgres",
        "postgresql://skillloop_apix:pw-leak-check@localhost:5432/postgres",
        "postgresql://skillloop_api_admin.ref:pw-leak-check@localhost:5432/postgres",
        "postgresql://localhost:5432/postgres",
        "mysql://skillloop_api:pw-leak-check@localhost:3306/db",
        "skillloop_api:pw-leak-check@localhost",
        "",
    ],
)
def test_database_url_rejects_anything_but_the_api_role(url: str) -> None:
    with pytest.raises(ValidationError) as exc_info:
        make_settings(database_url=url)

    # The error must never echo the connection string or its password.
    assert "pw-leak-check" not in str(exc_info.value)


def test_pool_defaults() -> None:
    settings = make_settings()

    assert settings.db_pool_min_size == 1
    assert settings.db_pool_max_size == 5
    assert settings.db_pool_timeout_seconds == 5
    assert settings.db_connect_timeout_seconds == 5


@pytest.mark.parametrize(
    "overrides",
    [
        {"db_pool_min_size": 0},
        {"db_pool_max_size": 0},
        {"db_pool_min_size": 6, "db_pool_max_size": 5},
        {"db_pool_timeout_seconds": 0},
        {"db_connect_timeout_seconds": 0},
    ],
)
def test_invalid_pool_bounds_are_rejected(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        make_settings(**overrides)
