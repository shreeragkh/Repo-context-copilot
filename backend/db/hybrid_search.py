from __future__ import annotations
import logging
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from typing import Any, Callable

logger = logging.getLogger(__name__)


class HybridSearchError(Exception):
    """Raised only when BOTH retrievers fail."""


def reciprocal_rank_fusion(
    bm25_results: list[dict], vector_results: list[dict],
    bm25_weight: float, vector_weight: float, rrf_k: int = 60,
) -> list[dict]:
    """Weighted RRF over two ranked lists, joined on chunk_id. Returns docs sorted by fused_score."""
    fused = {}
    for source, results, weight in (("bm25", bm25_results, bm25_weight),
                                    ("vector", vector_results, vector_weight)):
        for rank, doc in enumerate(results, start=1):
            text = doc.get("text") or doc.get("content") or ""
            doc_id = str(doc.get("chunk_id") or text.strip())
            entry = fused.setdefault(doc_id, {
                "chunk_id": doc_id, "text": text, "metadata": {},
                "bm25_score": 0.0, "vector_score": 0.0, "fused_score": 0.0,
            })
            if not entry["metadata"]:
                entry["metadata"] = doc.get("metadata") or {}
            if not entry["text"] and text:
                entry["text"] = text
            entry["fused_score"] += weight / (rrf_k + rank)
            entry[f"{source}_score"] = doc.get("similarity_score") or doc.get("score") or 0.0
    return sorted(fused.values(), key=lambda d: d["fused_score"], reverse=True)


class HybridSearch:
    """BM25 + vector retrieval run in parallel, fused with RRF. One retriever failing degrades gracefully."""

    def __init__(self, bm25_retriever, vector_retriever):
        self.bm25_retriever = bm25_retriever
        self.vector_retriever = vector_retriever

    def hybrid_retrieval(
        self, query_text: str, k: int = 10, fetch_k: int = 25,
        bm25_weight: float = 0.4, vector_weight: float = 0.6, rrf_k: int = 60,
        metadata_filter: Callable[[dict], bool] | None = None,
        timeout_s: float = 15.0,
    ) -> list[dict[str, Any]]:
        start = time.monotonic()
        bm25_results, vector_results = self._run_retrievers_with_fallback(query_text, fetch_k, timeout_s)
        fused = reciprocal_rank_fusion(bm25_results, vector_results, bm25_weight, vector_weight, rrf_k)
        if metadata_filter is not None:
            fused = [r for r in fused if metadata_filter(r.get("metadata", {}))]
        results = fused[:k]
        logger.info(
            "hybrid_search query=%r bm25=%d vector=%d fused=%d returned=%d latency_ms=%.0f",
            query_text, len(bm25_results), len(vector_results), len(fused), len(results),
            (time.monotonic() - start) * 1000, extra={"component": "hybrid_search"},
        )
        return results

    def _run_retrievers_with_fallback(self, query_text: str, fetch_k: int, timeout_s: float):
        def safe_call(fn, name: str) -> list[dict]:
            try:
                return fn(query_text, k=fetch_k)
            except Exception:
                logger.exception("Retriever %s failed", name, extra={"component": "hybrid_search"})
                return []

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = {"bm25": pool.submit(safe_call, self.bm25_retriever.query, "bm25"),
                       "vector": pool.submit(safe_call, self.vector_retriever.query, "vector")}
            results = {}
            for name, future in futures.items():
                try:
                    results[name] = future.result(timeout=timeout_s)
                except FutureTimeoutError:
                    logger.warning("%s retriever timed out after %.1fs", name, timeout_s,
                                   extra={"component": "hybrid_search"})
                    results[name] = []

        if not results["bm25"] and not results["vector"]:
            raise HybridSearchError(f"Both retrievers failed or timed out for query: {query_text!r}")
        return results["bm25"], results["vector"]
