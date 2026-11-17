"""Environment-validated application settings (Pydantic Settings v2)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent

DEFAULT_OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]


class Settings(BaseSettings):
    # backend/.env regardless of the working directory; a .env in the CWD overrides it.
    model_config = SettingsConfigDict(
        env_file=(BACKEND_DIR / ".env", ".env"), env_file_encoding="utf-8", extra="ignore"
    )

    # --- Database ---
    database_url: str = "postgresql+psycopg://calcipher:calcipher@localhost:5432/calcipher"
    db_pool_size: int = Field(5, ge=1, le=50)
    db_max_overflow: int = Field(5, ge=0, le=50)
    auto_create_schema: bool = True

    # --- OpenStreetMap Overpass ---
    overpass_enabled: bool = True
    overpass_urls: list[str] = Field(default_factory=lambda: list(DEFAULT_OVERPASS_URLS), min_length=1)
    overpass_user_agent: str = Field("CalCipher-UPM-Academic-Project/1.0", min_length=8)
    overpass_timeout_s: float = Field(20.0, gt=0, le=60)
    overpass_cache_ttl_hours: int = Field(24, ge=1, le=24 * 30)
    overpass_max_radius_m: int = Field(5000, ge=100, le=10_000)
    overpass_failure_cooldown_s: float = Field(300.0, ge=0, le=3600)

    # --- Embeddings ---
    embedder_backend: Literal["sentence-transformers", "hash"] = "sentence-transformers"
    embedding_model: str = "sentence-transformers/all-distilroberta-v1"

    # --- LLM (optional) ---
    gemini_api_key: SecretStr | None = None
    gemini_model: str = "gemini-2.5-flash"
    llm_timeout_s: float = Field(12.0, gt=0, le=60)

    # --- API ---
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173"]
    )
    rate_limit_per_minute: int = Field(30, ge=1, le=10_000)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    @field_validator("overpass_urls")
    @classmethod
    def _https_only(cls, urls: list[str]) -> list[str]:
        for url in urls:
            if not url.startswith("https://"):
                raise ValueError(f"Overpass URL must use https: {url!r}")
        return urls

    @field_validator("gemini_api_key", mode="before")
    @classmethod
    def _empty_key_is_none(cls, value: object) -> object:
        return None if value in ("", None) else value

    @property
    def llm_enabled(self) -> bool:
        return self.gemini_api_key is not None


@lru_cache
def get_settings() -> Settings:
    return Settings()
