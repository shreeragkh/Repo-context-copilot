import json
import logging
import re

import tiktoken

from llm_router import classifier_router, generation_router

logger = logging.getLogger(__name__)
enc = tiktoken.get_encoding("cl100k_base")

ABSTENTION = "Cannot be determined from the provided repository context."
RELEVANCE_THRESHOLD = -8.5
MODEL_CONTEXT_WINDOW = 12000

RETRIEVAL_BUDGET = {"LOW": 10, "MEDIUM": 20, "HIGH": 30}
DROPOFF_BY_COMPLEXITY = {"LOW": 0.08, "MEDIUM": 0.10, "HIGH": 0.12}

COMPLEXITY_PROMPT = """
You are a query complexity classifier for a code repository.

Classify the query into exactly one of: LOW, MEDIUM, HIGH.

LOW: answerable from one file/function/class/local section, no cross-file reasoning.
MEDIUM: requires understanding multiple related files/functions/components, or a limited flow.
HIGH: requires multi-component/subsystem reasoning, tracing a multi-step flow, or architectural understanding.

Return ONLY valid JSON: {{"complexity": "LOW|MEDIUM|HIGH", "confidence": 0.0, "reason": "brief"}}

User query:
{query}
"""


def count_tokens(text: str) -> int:
    return len(enc.encode(text))


def trim_to_token_budget(results, context_token_budget=None, tpm_limit=MODEL_CONTEXT_WINDOW,
                          reserved_output_tokens=512, prompt_overhead_tokens=150, safety_margin=300):
    if context_token_budget is None:
        context_token_budget = tpm_limit - reserved_output_tokens - prompt_overhead_tokens - safety_margin
    context_token_budget = max(context_token_budget, 200)
    kept, total = [], 0
    for r in results:
        t = count_tokens(r["text"])
        if kept and total + t > context_token_budget:
            break
        kept.append(r)
        total += t
    return kept


def adaptive_cutoff(reranked_results: list[dict], min_keep: int = 1, max_keep: int = 10,
                     complexity: str = "MEDIUM") -> list[dict]:
    dropoff_ratio = DROPOFF_BY_COMPLEXITY.get(complexity, 0.12)
    min_keep = {"LOW": 3, "MEDIUM": 4, "HIGH": 5}.get(complexity, min_keep)
    if not reranked_results:
        return []
    if len(reranked_results) <= min_keep:
        return reranked_results

    kept = [reranked_results[0]]
    for i in range(1, min(len(reranked_results), max_keep)):
        if len(kept) >= min_keep:
            prev_score = reranked_results[i - 1]["rerank_score"]
            curr_score = reranked_results[i]["rerank_score"]
            denom = max(abs(prev_score), 1e-6)
            drop = (prev_score - curr_score) / denom
            if drop > dropoff_ratio and len(kept) >= min_keep:
                break
        kept.append(reranked_results[i])
    return kept


def classify_complexity_heuristic(query: str) -> str | None:
    q = query.lower().strip()
    word_count = len(q.split())
    strong_high = ["trace", "end to end", "end-to-end", "architecture", "across", "interact"]
    if any(s in q for s in strong_high):
        return "HIGH"
    low_signals = ["what is", "where is", "define", "which file", "what does"]
    if word_count <= 10 and (any(s in q for s in low_signals) or q.startswith(("what ", "where ", "which "))):
        return "LOW"
    if word_count > 25 or " compare " in q or "impact" in q:
        return "HIGH"
    if word_count <= 15:
        return None
    return "MEDIUM"


def _parse_json_loose(text: str) -> dict:
    try:
        return json.loads(text)
    except Exception:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except Exception:
                pass
        return {}


def classify_complexity(query: str) -> tuple[str, float, str, dict]:
    """Returns (complexity, confidence, reason, model_info)."""
    heuristic_result = classify_complexity_heuristic(query)
    if heuristic_result is not None:
        return heuristic_result, 1.0, "heuristic", {"model_name": "heuristic", "provider": "local", "paid": False}

    prompt = COMPLEXITY_PROMPT.format(query=query)
    try:
        result = classifier_router.invoke(prompt)
        data = _parse_json_loose(result.content)
        complexity = str(data.get("complexity", "MEDIUM")).upper()
        if complexity not in {"LOW", "MEDIUM", "HIGH"}:
            complexity = "MEDIUM"
        return (
            complexity, float(data.get("confidence", 0.5)), data.get("reason", "llm_fallback"),
            {"model_name": result.model_name, "provider": result.provider, "paid": result.paid},
        )
    except Exception as e:
        logger.warning("Complexity classification unavailable, defaulting to MEDIUM: %s", e,
                        extra={"component": "pipeline"})
        return "MEDIUM", 0.0, "llm_unavailable", {"model_name": "none", "provider": "none", "paid": False}


def _empty_result(message, complexity="UNKNOWN", complexity_conf=0.0, complexity_reason="",
                   fetch_k=0, return_context=False, context="", model_info=None):
    result = {
        "answer": message, "sources": [], "confidence": 0.0,
        "complexity": complexity, "complexity_confidence": complexity_conf,
        "complexity_reason": complexity_reason, "retrieval_k": fetch_k, "final_chunk_count": 0,
        "model_info": model_info or {"model_name": "none", "provider": "none", "paid": False},
    }
    if return_context:
        result["context"] = context
    return result


def rag_pipeline(query: str, hybrid_search, reranker=None, top_k=None, top_n=None,
                  min_score: float = 0.2, return_context: bool = False, use_adaptive: bool = True):
    """Hybrid retrieval + reranking + (optional) adaptive cutoff + generation."""

    complexity, complexity_conf, complexity_reason, _classifier_info = classify_complexity(query)

    fetch_k = top_k or RETRIEVAL_BUDGET.get(complexity, RETRIEVAL_BUDGET["MEDIUM"])
    results = hybrid_search.hybrid_retrieval(query, k=fetch_k)
    if not results:
        return _empty_result(ABSTENTION, complexity, complexity_conf, complexity_reason, fetch_k, return_context)

    bm25_weight, vector_weight, rrf_k = 0.4, 0.6, 60
    max_rrf_score = (bm25_weight + vector_weight) / (rrf_k + 1)
    results = [d for d in results if (d.get("fused_score", 0.0) / max_rrf_score) >= min_score]
    if not results:
        return _empty_result(ABSTENTION, complexity, complexity_conf, complexity_reason, fetch_k, return_context)

    if reranker is not None:
        results = reranker.rerank(query, results, top_n=(top_n or fetch_k))

    if use_adaptive and results:
        results = adaptive_cutoff(results, complexity=complexity)
    if not results:
        return _empty_result(ABSTENTION, complexity, complexity_conf, complexity_reason, fetch_k, return_context)

    # de-dup
    unique, seen = [], set()
    for doc in results:
        key = (doc.get("metadata", {}).get("file_path", "unknown"), doc.get("text", "").strip())
        if key not in seen:
            seen.add(key)
            unique.append(doc)
    results = unique

    prompt_overhead = count_tokens(query) + 80
    results = trim_to_token_budget(results, tpm_limit=MODEL_CONTEXT_WINDOW,
                                    reserved_output_tokens=512, prompt_overhead_tokens=prompt_overhead,
                                    safety_margin=200)
    if not results:
        return _empty_result(ABSTENTION, complexity, complexity_conf, complexity_reason, fetch_k, return_context)

    context = "\n\n".join(doc.get("text", "") for doc in results).strip()

    sources = [{
        "source": doc.get("metadata", {}).get("file_path", "unknown"),
        "score": doc.get("fused_score", 0.0) / max_rrf_score,
        "preview": doc.get("text", "")[:120] + "...",
    } for doc in results]

    confidence = max((doc.get("fused_score", 0.0) / max_rrf_score for doc in results), default=0.0)
    best_rerank_score = max((doc.get("rerank_score", float("-inf")) for doc in results), default=float("-inf"))

    model_info = {"model_name": "none", "provider": "none", "paid": False}

    if not context or best_rerank_score < RELEVANCE_THRESHOLD:
        answer = ABSTENTION
    else:
        prompt = f"""Answer the question using only the repository context below.

Answer directly in 1-3 concise sentences.
Use relevant file names, functions, classes, endpoints, and code from the context.
Do not use outside knowledge.

Repository context:
{context}

Question:
{query}

Answer:"""
        try:
            result = generation_router.invoke(prompt)
            answer = re.sub(r"<think>.*?</think>", "", result.content, flags=re.DOTALL).strip() or ABSTENTION
            model_info = {"model_name": result.model_name, "provider": result.provider, "paid": result.paid}
        except Exception as exc:
            return _empty_result(f"Generation failed: {exc}", complexity, complexity_conf, complexity_reason,
                                  fetch_k, return_context, context, model_info)

    output = {
        "answer": answer, "sources": sources, "confidence": confidence,
        "complexity": complexity, "complexity_confidence": complexity_conf,
        "complexity_reason": complexity_reason, "retrieval_k": fetch_k,
        "final_chunk_count": len(results), "model_info": model_info,
    }
    if return_context:
        output["context"] = context
    return output
