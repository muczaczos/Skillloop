from urllib.parse import unquote, urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# The only database role the backend may log in as (see supabase/migrations). Through the
# Supavisor pooler the user is written "<role>.<project-ref>".
API_DATABASE_ROLE = "skillloop_api"


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # hide_input_in_errors: a validation error must never echo a value such as DATABASE_URL.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", hide_input_in_errors=True)

    environment: str = "development"
    # Comma-separated list of origins allowed to call the API from a browser.
    backend_cors_origins: str = Field(default="http://localhost:3000")

    # Supabase project URL, e.g. https://<project-ref>.supabase.co (required).
    supabase_url: str
    # Expected "aud" claim of Supabase access tokens.
    supabase_jwt_audience: str = "authenticated"
    # Where the public signing keys (JWKS) are downloaded from. Leave empty to derive it from
    # SUPABASE_URL. Set it only when the backend reaches Supabase under another address than the
    # one in the "iss" claim, e.g. a container calling a local stack on host.docker.internal.
    # It does not change the expected issuer.
    supabase_jwks_url: str = ""

    # PostgreSQL connection string of the Supavisor SESSION pooler, logging in as the
    # dedicated skillloop_api role (required). It is a secret: never log or print it.
    database_url: SecretStr
    # Connection pool (see app/db/pool.py). Every connection occupies one server-side
    # connection of the session pooler, so keep max_size small.
    db_pool_min_size: int = Field(default=1, ge=1)
    db_pool_max_size: int = Field(default=5, ge=1)
    # How long a request waits for a free connection before the API answers 503.
    db_pool_timeout_seconds: float = Field(default=5, gt=0)
    db_connect_timeout_seconds: int = Field(default=5, ge=1)

    @field_validator("database_url")
    @classmethod
    def _validate_database_url(cls, value: SecretStr) -> SecretStr:
        # Messages never include the URL itself (it contains the password).
        try:
            parts = urlsplit(value.get_secret_value())
            username = unquote(parts.username or "")
        except ValueError:
            raise ValueError("DATABASE_URL is not a valid URL") from None
        if parts.scheme not in ("postgresql", "postgres"):
            raise ValueError("DATABASE_URL must start with postgresql://")
        # Any other role (e.g. postgres) would bypass Row Level Security.
        if username != API_DATABASE_ROLE and not username.startswith(f"{API_DATABASE_ROLE}."):
            raise ValueError(f"DATABASE_URL must log in as the {API_DATABASE_ROLE} role")
        return value

    @model_validator(mode="after")
    def _validate_pool_bounds(self) -> "Settings":
        if self.db_pool_min_size > self.db_pool_max_size:
            raise ValueError("DB_POOL_MIN_SIZE must not exceed DB_POOL_MAX_SIZE")
        return self

    @field_validator("supabase_url")
    @classmethod
    def _normalize_supabase_url(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if not value.startswith(("https://", "http://")):
            raise ValueError("SUPABASE_URL must be an http(s) URL")
        return value

    @field_validator("supabase_jwks_url")
    @classmethod
    def _validate_jwks_url(cls, value: str) -> str:
        value = value.strip()
        if value and not value.startswith(("https://", "http://")):
            raise ValueError("SUPABASE_JWKS_URL must be an http(s) URL")
        return value

    @model_validator(mode="after")
    def _default_jwks_url(self) -> "Settings":
        if not self.supabase_jwks_url:
            self.supabase_jwks_url = f"{self.supabase_jwt_issuer}/.well-known/jwks.json"
        return self

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.backend_cors_origins.split(",") if o.strip()]

    @property
    def supabase_jwt_issuer(self) -> str:
        """Value of the "iss" claim in tokens issued by Supabase Auth."""
        return f"{self.supabase_url}/auth/v1"


settings = Settings()
