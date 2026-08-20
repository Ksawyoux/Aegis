from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings loaded from ``AEGIS_``-prefixed environment variables."""

    model_config = SettingsConfigDict(env_prefix="AEGIS_", frozen=True)

    database_url: str = "postgresql+psycopg://aegis:aegis@localhost:5433/aegis"
    ollama_base_url: str = "http://localhost:11434"
    embedding_model: str = "bge-m3"
    anthropic_model: str = "claude-opus-5"
    agent_effort: Literal["low", "medium", "high", "xhigh", "max"] = "high"
    # Aggregates-only tool access means one telemetry call per service to
    # establish cross-service correlation, so multi-service incidents need
    # more turns than a single-service one.
    agent_max_turns: int = 20
    slack_webhook_url: str | None = None
    github_webhook_secret: str | None = None
    corpus_dir: Path = Path("corpus")
    baseline_sparse_threshold: int = 50
    hunk_max_files: int = 15
    hunk_max_hunks_per_file: int = 3
    hunk_max_lines_per_file: int = 60
    embedding_dim: Literal[1024] = 1024
    rollup_bucket_seconds: Literal[60] = 60
