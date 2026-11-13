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
