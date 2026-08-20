"""
Tries Groq's free-tier models first (in the order configured in
GROQ_FREE_MODELS). If a model comes back rate-limited / quota-exceeded, it's
put in cooldown until the next UTC day and the router moves on to the next
free model. Once every free model is in cooldown, it falls back to the paid
OpenAI model for that purpose (classification vs generation).

Every call to `.invoke()` returns which model actually answered and whether
it was paid, so callers can log it.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from langchain_groq import ChatGroq
from langchain_openai import ChatOpenAI

from config import settings

logger = logging.getLogger(__name__)

RATE_LIMIT_MARKERS = ("rate limit", "rate_limit", "429", "quota", "too many requests")


@dataclass
class ModelResult:
    content: str
    model_name: str
    provider: str
    paid: bool


def _is_rate_limit_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(marker in msg for marker in RATE_LIMIT_MARKERS)


class _Cooldowns:
    """Tracks which model names are temporarily unusable."""

    def __init__(self):
        self._until: dict[str, datetime] = {}

    def is_cool(self, name: str) -> bool:
        until = self._until.get(name)
        return until is not None and datetime.now(timezone.utc) < until

    def mark(self, name: str, hours: float = 6.0):
        self._until[name] = datetime.now(timezone.utc) + timedelta(hours=hours)
        logger.warning("Model %s put in cooldown until %s", name, self._until[name],
                        extra={"component": "llm_router"})

    def status(self) -> dict[str, str]:
        return {k: v.isoformat() for k, v in self._until.items() if self.is_cool(k)}


class ModelRouter:
    def __init__(self, purpose: str, paid_model_name: str, max_tokens: int = 512):
        """purpose: free-text label used only for logging ('classification' | 'generation')."""
        self.purpose = purpose
        self.paid_model_name = paid_model_name
        self.max_tokens = max_tokens
        self.cooldowns = _Cooldowns()

        self._free_clients: dict[str, ChatGroq] = {
            name: ChatGroq(model=name, temperature=0, max_tokens=max_tokens,
                            api_key=settings.GROQ_API_KEY)
            for name in settings.GROQ_FREE_MODELS
        }
        self._paid_client = ChatOpenAI(
            model=paid_model_name, temperature=0, max_tokens=max_tokens,
            reasoning_effort="minimal", api_key=settings.OPENAI_API_KEY,
        )

    def invoke(self, prompt: str, **kwargs) -> ModelResult:
        last_error: Exception | None = None

        for name in settings.GROQ_FREE_MODELS:
            if self.cooldowns.is_cool(name):
                continue
            try:
                response = self._free_clients[name].invoke(prompt, **kwargs)
                return ModelResult(
                    content=self._extract_text(response), model_name=name,
                    provider="groq", paid=False,
                )
            except Exception as e:
                last_error = e
                if _is_rate_limit_error(e):
                    self.cooldowns.mark(name)
                    continue
                logger.warning("Groq model %s failed (non-rate-limit): %s", name, e,
                                extra={"component": "llm_router"})
                continue

        # All free models exhausted or unavailable -> paid fallback
        logger.info("Falling back to paid model %s for %s", self.paid_model_name, self.purpose,
                     extra={"component": "llm_router"})
        try:
            response = self._paid_client.invoke(prompt, **kwargs)
            return ModelResult(
                content=self._extract_text(response), model_name=self.paid_model_name,
                provider="openai", paid=True,
            )
        except Exception as e:
            logger.exception("Paid fallback model also failed for %s", self.purpose,
                              extra={"component": "llm_router"})
            raise RuntimeError(
                f"All models unavailable for {self.purpose}. Last free-tier error: {last_error}; "
                f"paid fallback error: {e}"
            ) from e

    @staticmethod
    def _extract_text(response: Any) -> str:
        content = response.content
        if isinstance(content, list):
            content = "".join(
                part.get("text", "") if isinstance(part, dict) else str(part) for part in content
            )
        return str(content or "").strip()


classifier_router = ModelRouter(
    purpose="classification", paid_model_name=settings.CLASSIFIER_PAID_MODEL, max_tokens=128,
)
generation_router = ModelRouter(
    purpose="generation", paid_model_name=settings.GENERATION_PAID_MODEL, max_tokens=512,
)
