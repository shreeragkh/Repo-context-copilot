from __future__ import annotations
import logging
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from typing import Any, Callable

logger = logging.getLogger(__name__)


class HybridSearchError(Exception):
    """Raised only when BOTH retrievers fail."""


def _reciprocal_rank_fusion(
    bm25_results: list[dict], vector_results: list[dict],
    bm25_weight: float, vector_weight: float, rrf_k: int = 60, id_key: str = "chunk_id",
) -> list[dict]:
    fused_docs = {}

    def get_id(doc):
        if doc.get(id_key):
            return str(doc[id_key])
        meta = doc.get("metadata") or {}
        if id_key in meta and meta[id_key]:
            return str(meta[id_key])
        return (doc.get("text") or doc.get("content") or "").strip()

    for rank, doc in enumerate(bm25_results):
        doc_id = get_id(doc)
        fused_docs[doc_id] = {
            "chunk_id": doc.get(id_key) or doc_id,
            "text": doc.get("text") or doc.get("content") or "",
            "metadata": doc.get("metadata") or {},
            "bm25_score": doc.get("score", 0.0),
            "vector_score": 0.0,
            "bm25_rrf": bm25_weight * (1.0 / (rrf_k + (rank + 1))),
            "vector_rrf": 0.0,
        }

    for rank, doc in enumerate(vector_results):
        doc_id = get_id(doc)
        text = doc.get("text") or doc.get("content") or ""
        metadata = doc.get("metadata") or {}
        score = doc.get("similarity_score") or doc.get("score") or 0.0

        if doc_id in fused_docs:
            fused_docs[doc_id]["vector_score"] = score
            fused_docs[doc_id]["vector_rrf"] = vector_weight * (1.0 / (rrf_k + (rank + 1)))
            if not fused_docs[doc_id]["metadata"] and metadata:
                fused_docs[doc_id]["metadata"] = metadata
            if not fused_docs[doc_id]["text"] and text:
                fused_docs[doc_id]["text"] = text
        else:
            fused_docs[doc_id] = {
                "chunk_id": doc.get(id_key) or doc_id,
                "text": text,
                "metadata": metadata,
                "bm25_score": 0.0,
                "vector_score": score,
                "bm25_rrf": 0.0,
                "vector_rrf": vector_weight * (1.0 / (rrf_k + (rank + 1))),
            }

    output = []
    for doc_id, info in fused_docs.items():
        fused_score = info["bm25_rrf"] + info["vector_rrf"]
        output.append({
            "chunk_id": info["chunk_id"], "text": info["text"], "metadata": info["metadata"],
            "fused_score": fused_score, "bm25_score": info["bm25_score"], "vector_score": info["vector_score"],
        })
    output.sort(key=lambda x: x["fused_score"], reverse=True)
    return output


class HybridSearch:
    def __init__(self, bm25_retriever, vector_retriever):
        self.bm25_retriever = bm25_retriever
        self.vector_retriever = vector_retriever

    def hybrid_retrieval(
        self, query_text: str, k: int = 10, fetch_k: int = 25,
        bm25_weight: float = 0.4, vector_weight: float = 0.6, rrf_k: int = 60,
        id_key: str = "chunk_id", metadata_filter: Callable[[dict], bool] | None = None,
        timeout_s: float = 15.0,
    ) -> list[dict[str, Any]]:
        start = time.monotonic()
        bm25_results, vector_results = self._run_retrievers_with_fallback(query_text, fetch_k, timeout_s)
        fused = _reciprocal_rank_fusion(bm25_results, vector_results, bm25_weight, vector_weight, rrf_k, id_key)
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

        with ThreadPoolExecutor(max_workers=2) as executor:
            bm25_future = executor.submit(safe_call, self.bm25_retriever.query, "bm25")
            vector_future = executor.submit(safe_call, self.vector_retriever.query, "vector")
            try:
                bm25_results = bm25_future.result(timeout=timeout_s)
            except FutureTimeoutError:
                bm25_results = []
            try:
                vector_results = vector_future.result(timeout=timeout_s)
            except FutureTimeoutError:
                vector_results = []

        if not bm25_results and not vector_results:
            raise HybridSearchError(f"Both retrievers failed or timed out for query: {query_text!r}")
        return bm25_results, vector_results
