from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    database_url: str = "postgresql+psycopg://bravobot:bravobot@localhost:5432/bravobot"

    groq_api_key: str | None = None
    groq_model: str = "llama-3.3-70b-versatile"
    cerebras_api_key: str | None = None
    cerebras_model: str = "llama-3.3-70b"
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-haiku-4-5"

    llm_provider_order: str = "groq,cerebras,anthropic"

    embedding_model: str = "intfloat/multilingual-e5-large"
    embedding_dim: int = 1024

    retrieval_top_k: int = 12
    retrieval_candidates: int = 30

    confidence_top1_min: float = 0.32
    confidence_top3_mean_min: float = 0.28
    confidence_consistency_min: float = 0.5
    confidence_keyword_coverage_min: float = 0.4
    confidence_signals_required: int = 3
    confidence_catastrophic_min: float = 0.20

    session_history_turns: int = 6

    cors_origins: str = "*"
    log_level: str = "INFO"

    @property
    def provider_order(self) -> list[str]:
        return [p.strip().lower() for p in self.llm_provider_order.split(",") if p.strip()]

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    def available_providers(self) -> list[str]:
        ordered = self.provider_order
        keys = {
            "groq": self.groq_api_key,
            "cerebras": self.cerebras_api_key,
            "anthropic": self.anthropic_api_key,
        }
        return [p for p in ordered if keys.get(p)]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
