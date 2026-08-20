from __future__ import annotations
import logging
import time
from typing import Any, Protocol

from config import settings

logger = logging.getLogger(__name__)


class ScoringBackend(Protocol):
    def predict(self, pairs: list[tuple[str, str]]) -> list[float]: ...


class RerankerError(Exception):
    pass


class Reranker:
    def __init__(self, model_name: str = None, batch_size: int = 32,
                 device: str | None = None, backend: ScoringBackend | None = None):
        self.batch_size = batch_size
        self.model_name = model_name or settings.RERANKER_MODEL
        self.backend = backend or self._load_local_model(self.model_name, device)

    @staticmethod
    def _load_local_model(model_name: str, device: str | None):
        from sentence_transformers import CrossEncoder
        logger.info("Loading cross-encoder reranker model: %s", model_name, extra={"component": "reranker"})
        return CrossEncoder(model_name, device=device)

    def rerank(self, query: str, candidates: list[dict[str, Any]], top_n: int = 5,
               min_score: float | None = None, text_key: str = "text",
               fallback_on_error: bool = True) -> list[dict[str, Any]]:
        if not candidates:
            return []

        start = time.monotonic()
        pairs = [(query, c[text_key]) for c in candidates]
        try:
            scores = self._score_in_batches(pairs)
        except Exception:
            logger.exception("Reranking failed for query=%r", query, extra={"component": "reranker"})
            if fallback_on_error:
                return [{**c, "rerank_score": c.get("fused_score", 0.0)} for c in candidates[:top_n]]
            raise RerankerError(f"Reranking failed for query: {query!r}")

        scored = [{**cand, "rerank_score": float(score)} for cand, score in zip(candidates, scores)]
        scored.sort(key=lambda c: c["rerank_score"], reverse=True)
        if min_score is not None:
            scored = [c for c in scored if c["rerank_score"] >= min_score]
        results = scored[:top_n]
        logger.info("rerank query=%r candidates=%d returned=%d latency_ms=%.0f",
                    query, len(candidates), len(results), (time.monotonic() - start) * 1000,
                    extra={"component": "reranker"})
        return results

    def _score_in_batches(self, pairs: list[tuple[str, str]]) -> list[float]:
        scores: list[float] = []
        for i in range(0, len(pairs), self.batch_size):
            batch = pairs[i: i + self.batch_size]
            scores.extend(float(s) for s in self.backend.predict(batch))
        return scores
