from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True,
    )

    LLM_PROVIDER: Literal["anthropic", "openai"] = "anthropic"
    ANTHROPIC_API_KEY: str | None = None
    OPENAI_API_KEY: str | None = None
    # When true AND the secondary provider's API key is also set, every LLM
    # client is wrapped in `FallbackLLMClient`. On a terminal failure from the
    # primary (after its retry-with-backoff loop is exhausted), the secondary
    # provider runs the same call once before the error propagates.
    LLM_ENABLE_FALLBACK: bool = True

    MODEL_PLANNER: str = "claude-sonnet-4-6"
    MODEL_RESPONSE: str = "claude-haiku-4-5"
    MODEL_PREPROCESSOR: str = "claude-haiku-4-5"

    OPENAI_MODEL_PLANNER: str = "gpt-4.1"
    OPENAI_MODEL_RESPONSE: str = "gpt-4.1-mini"
    OPENAI_MODEL_PREPROCESSOR: str = "gpt-4.1-mini"

    EMBEDDING_MODEL: str = "all-MiniLM-L6-v2"

    DATA_DIR: Path = Path("data")
    AUDIT_LOG_PATH: Path = Path("logs/audit.ndjson")
    KB_INDEX_PATH: Path | None = None

    SESSION_TTL_SECONDS: int = 1800
    PROPOSAL_TTL_SECONDS: int = 600

    RATE_LIMIT_PER_MIN: int = 60

    # Comma-separated string in .env (e.g. "http://localhost:3000,https://acme.com")
    # OR a JSON array. Use the `cors_origins` property to read it as a list.
    CORS_ALLOWED_ORIGINS: str = "http://localhost:3000"

    MCP_TRANSPORT: Literal["sse", "stdio"] = "sse"
    MCP_HOST: str = "0.0.0.0"
    MCP_PORT: int = 8001
    # Comma-separated. FastMCP applies DNS-rebinding protection at the SSE
    # transport. When the server runs behind a TLS-terminating proxy (Fly, etc.)
    # the Host header in incoming requests is the public hostname, not the
    # bind address — without an explicit allowlist FastMCP returns 421
    # "Invalid Host header". Keep localhost entries for local Inspector use.
    MCP_ALLOWED_HOSTS: str = (
        "itsm-bridge-backend.fly.dev:8001,"
        "itsm-bridge-backend.fly.dev,"
        "localhost:*,"
        "127.0.0.1:*,"
        "[::1]:*"
    )

    @property
    def mcp_allowed_hosts(self) -> list[str]:
        return [p.strip() for p in (self.MCP_ALLOWED_HOSTS or "").split(",") if p.strip()]

    ADMIN_TOKEN: str | None = None

    @property
    def cors_origins(self) -> list[str]:
        s = (self.CORS_ALLOWED_ORIGINS or "").strip()
        if not s:
            return []
        if s.startswith("["):
            try:
                return [str(x).strip() for x in json.loads(s) if str(x).strip()]
            except json.JSONDecodeError:
                return [s]
        return [p.strip() for p in s.split(",") if p.strip()]


def get_settings() -> Settings:
    return Settings()
