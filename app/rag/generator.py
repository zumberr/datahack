"""
Multi-provider LLM client with automatic fallback.

Order (configurable via LLM_PROVIDER_ORDER env): groq → cerebras → anthropic.
If a provider fails or is not configured, the next one is attempted.
"""
from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Protocol

from app.config import get_settings

logger = logging.getLogger("bravobot.generator")


class LLMError(RuntimeError):
    pass


class LLMClient(Protocol):
    name: str

    def complete(self, system: str, user: str, *, temperature: float = 0.2,
                 max_tokens: int = 800) -> str: ...


class GroqClient:
    name = "groq"

    def __init__(self, api_key: str, model: str) -> None:
        from groq import Groq
        self._client = Groq(api_key=api_key)
        self._model = model

    def complete(self, system: str, user: str, *, temperature: float = 0.2,
                 max_tokens: int = 800) -> str:
        resp = self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return (resp.choices[0].message.content or "").strip()


class CerebrasClient:
    name = "cerebras"

    def __init__(self, api_key: str, model: str) -> None:
        from cerebras.cloud.sdk import Cerebras
        self._client = Cerebras(api_key=api_key)
        self._model = model

    def complete(self, system: str, user: str, *, temperature: float = 0.2,
                 max_tokens: int = 800) -> str:
        resp = self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return (resp.choices[0].message.content or "").strip()


class AnthropicClient:
    name = "anthropic"

    def __init__(self, api_key: str, model: str) -> None:
        from anthropic import Anthropic
        self._client = Anthropic(api_key=api_key)
        self._model = model

    def complete(self, system: str, user: str, *, temperature: float = 0.2,
                 max_tokens: int = 800) -> str:
        msg = self._client.messages.create(
            model=self._model,
            system=system,
            messages=[{"role": "user", "content": user}],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        parts = [block.text for block in msg.content if getattr(block, "type", "") == "text"]
        return "\n".join(parts).strip()


def _build_client(name: str):
    settings = get_settings()
    if name == "groq" and settings.groq_api_key:
        return GroqClient(settings.groq_api_key, settings.groq_model)
    if name == "cerebras" and settings.cerebras_api_key:
        return CerebrasClient(settings.cerebras_api_key, settings.cerebras_model)
    if name == "anthropic" and settings.anthropic_api_key:
        return AnthropicClient(settings.anthropic_api_key, settings.anthropic_model)
    return None


class MultiProviderLLM:
    def __init__(self, order: Iterable[str] | None = None) -> None:
        settings = get_settings()
        names = list(order) if order else settings.provider_order
        self._clients: list[LLMClient] = []
        for n in names:
            try:
                client = _build_client(n)
            except Exception as exc:
                logger.warning("Failed to init provider %s: %s", n, exc)
                continue
            if client is not None:
                self._clients.append(client)

    @property
    def available(self) -> list[str]:
        return [c.name for c in self._clients]

    def complete(self, system: str, user: str, *, temperature: float = 0.2,
                 max_tokens: int = 800) -> str:
        if not self._clients:
            raise LLMError("No LLM providers configured. Set at least one API key in .env.")

        last_error: Exception | None = None
        for client in self._clients:
            try:
                logger.info("LLM call via %s", client.name)
                return client.complete(system, user, temperature=temperature, max_tokens=max_tokens)
            except Exception as exc:
                last_error = exc
                logger.warning("Provider %s failed: %s", client.name, exc)
                continue

        raise LLMError(f"All providers failed; last error: {last_error}")


_instance: MultiProviderLLM | None = None


def get_llm() -> MultiProviderLLM:
    global _instance
    if _instance is None:
        _instance = MultiProviderLLM()
    return _instance
