from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = "development"
    # Comma-separated list of origins allowed to call the API from a browser.
    backend_cors_origins: str = Field(default="http://localhost:3000")

    # Supabase project URL, e.g. https://<project-ref>.supabase.co (required).
    supabase_url: str
    # Expected "aud" claim of Supabase access tokens.
    supabase_jwt_audience: str = "authenticated"

    @field_validator("supabase_url")
    @classmethod
    def _normalize_supabase_url(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if not value.startswith(("https://", "http://")):
            raise ValueError("SUPABASE_URL must be an http(s) URL")
        return value

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.backend_cors_origins.split(",") if o.strip()]

    @property
    def supabase_jwt_issuer(self) -> str:
        """Value of the "iss" claim in tokens issued by Supabase Auth."""
        return f"{self.supabase_url}/auth/v1"

    @property
    def supabase_jwks_url(self) -> str:
        """Endpoint publishing the public keys used to verify Supabase access tokens."""
        return f"{self.supabase_jwt_issuer}/.well-known/jwks.json"


settings = Settings()
