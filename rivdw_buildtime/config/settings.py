"""Loads all configuration from .env and exposes a typed Settings object."""

from __future__ import annotations

from functools import lru_cache
from typing import List, Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseConfig:
    """Runtime config for one source database — metadata from databases.json, URL from env."""

    def __init__(
        self,
        name: str,
        display_name: str,
        db_type: str,
        schema: str,
        domain_tag: str,
        owner: str,
        description: str,
        connection_string: str,
    ) -> None:
        self.name = name
        self.display_name = display_name
        self.db_type = db_type
        self.schema = schema
        self.domain_tag = domain_tag
        self.owner = owner
        self.description = description
        self._connection_string = connection_string

    def connection_url(self) -> str:
        """Return the SQLAlchemy URL exactly as written in the .env file."""
        return self._connection_string


class Settings(BaseSettings):
    """All application configuration. Reads from environment / .env file."""

    # extra="allow" causes pydantic-settings to capture every unknown .env key in
    # model_extra — this is how dynamic database connection strings are read.
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="allow")

    # --- Groq LLM ---
    groq_api_key: str = Field(default="", alias="GROQ_API_KEY")
    groq_model: str = Field(default="llama-3.3-70b-versatile", alias="GROQ_MODEL")
    groq_enabled: bool = Field(default=True, alias="GROQ_ENABLED")

    # --- VS Code LM API fallback ---
    vscode_lm_port: int = Field(default=50234, alias="VSCODE_LM_PORT")
    vscode_lm_secret: str = Field(default="abc123", alias="VSCODE_LM_SECRET")

    # --- Qdrant vector store ---
    qdrant_path: str = Field(default="./qdrant_data", alias="QDRANT_PATH")
    qdrant_collection: str = Field(default="rivdw_metadata", alias="QDRANT_COLLECTION")

    # --- FastEmbed ---
    embedding_model: str = Field(default="BAAI/bge-small-en-v1.5", alias="EMBEDDING_MODEL")

    # --- SQLite relational store ---
    sqlite_db_path: str = Field(default="./rivdw_app.db", alias="SQLITE_DB_PATH")

    # --- Snapshots ---
    snapshot_dir: str = Field(default="./snapshots", alias="SNAPSHOT_DIR")

    # --- Pipeline ---
    enrichment_batch_size: int = Field(default=20, alias="ENRICHMENT_BATCH_SIZE")
    min_description_word_count: int = Field(default=20, alias="MIN_DESCRIPTION_WORD_COUNT")
    guardian_filler_phrases: str = Field(
        default="this column contains data,stores information,contains values",
        alias="GUARDIAN_FILLER_PHRASES",
    )

    # --- Known domains ---
    known_domains: str = Field(
        default="compliance,surveillance,employee,brokerage,finance,risk",
        alias="KNOWN_DOMAINS",
    )

    # --- Path to the databases metadata JSON (non-secret, safe to commit) ---
    databases_config_path: str = Field(
        default="./config/databases.json",
        alias="DATABASES_CONFIG_PATH",
    )

    def get_filler_phrases(self) -> List[str]:
        """Return guardian filler phrases as a list."""
        return [p.strip() for p in self.guardian_filler_phrases.split(",") if p.strip()]

    def get_known_domains(self) -> List[str]:
        """Return known domain tags as a list."""
        return [d.strip() for d in self.known_domains.split(",") if d.strip()]

    def get_connection_string(self, db_name: str) -> Optional[str]:
        """
        Look up the SQLAlchemy connection URL for a database by name.

        The .env file must contain a line whose key matches db_name exactly:
            compliance_db=mssql+pyodbc:///?odbc_connect=...

        pydantic-settings captures all unknown .env keys in model_extra (lowercased),
        so both 'compliance_db' and 'COMPLIANCE_DB' in .env resolve correctly.
        """
        extras = self.model_extra or {}
        return extras.get(db_name.lower()) or extras.get(db_name)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the singleton Settings instance (cached after first call)."""
    return Settings()
