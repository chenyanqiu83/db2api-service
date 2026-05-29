from __future__ import annotations

from functools import lru_cache

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _parse_csv(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return [str(value).strip()] if str(value).strip() else []


class Settings(BaseSettings):
    app_name: str = "db2api-service"
    database_url: str = Field(
        default="sqlite:///./demo.db",
        validation_alias=AliasChoices("DATABASE_URL", "DB_URL"),
    )
    schema_name: str | None = Field(
        default=None,
        validation_alias=AliasChoices("SCHEMA_NAME", "DB_SCHEMA"),
    )
    include_tables: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("INCLUDE_TABLES"),
    )
    exclude_tables: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("EXCLUDE_TABLES"),
    )
    default_page_size: int = Field(
        default=50,
        ge=1,
        validation_alias=AliasChoices("DEFAULT_PAGE_SIZE"),
    )
    max_page_size: int = Field(
        default=200,
        ge=1,
        validation_alias=AliasChoices("MAX_PAGE_SIZE"),
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
        populate_by_name=True,
    )

    @field_validator("include_tables", "exclude_tables", mode="before")
    @classmethod
    def split_csv(cls, value: object) -> list[str]:
        return _parse_csv(value)

    @field_validator("database_url", mode="before")
    @classmethod
    def normalize_sqlite_windows_path(cls, value: object) -> object:
        if not isinstance(value, str):
            return value

        candidate = value.strip()
        if len(candidate) >= 3 and candidate[1:3] == ":\\":
            return f"sqlite:///{candidate.replace('\\', '/')}"
        if candidate.startswith("sqlite:///") and "\\" in candidate:
            return "sqlite:///" + candidate[len("sqlite:///") :].replace("\\", "/")
        return candidate


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
