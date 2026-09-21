import json
import logging
import re

import tiktoken

from config import settings
from llm_router import classifier_router, generation_router

logger = logging.getLogger(__name__)
enc = tiktoken.get_encoding("cl100k_base")

ABSTENTION = "Cannot be determined from the provided repository context."

# Tuned to MiniLM cross-encoder raw logits; recalibrate if the reranker changes.
RELEVANCE_THRESHOLD = settings.RELEVANCE_THRESHOLD

MODEL_CONTEXT_WINDOW = 12000

RETRIEVAL_BUDGET = {"LOW": 10, "MEDIUM": 20, "HIGH": 30}
DROPOFF_BY_COMPLEXITY = {"LOW": 0.08, "MEDIUM": 0.10, "HIGH": 0.12}
MIN_KEEP_BY_COMPLEXITY = {"LOW": 3, "MEDIUM": 4, "HIGH": 5}

COMPLEXITY_PROMPT = """
You are a query complexity classifier for a code repository.

Your task is to determine how much repository context is likely
required to answer the user's query.

Classify the query into exactly one of these levels:

LOW:
- Can probably be answered from one file, function, class, or
  small local section.
- Does not require significant cross-file reasoning.

MEDIUM:
- Requires understanding multiple related files, functions,
  or components.
- May require following a limited data or execution flow.

HIGH:
- Requires understanding multiple components or subsystems.
- Requires tracing a multi-step execution or data flow.
- Requires architectural or dependency reasoning.
- Requires understanding how several parts of the repository interact.

Consider:
1. Number of components involved
2. Number of files likely to be required
3. Whether the query requires tracing a flow
4. Whether cross-file reasoning is required
5. Whether architectural reasoning is required
6. Whether multiple steps need to be understood
7. Whether the query asks for comparison or impact analysis

Do not classify based only on query length.

Return ONLY valid JSON:

{{
    "complexity": "LOW | MEDIUM | HIGH",
    "confidence": 0.0,
    "reason": "Brief explanation"
}}

User query:
{query}
"""


def count_tokens(text: str) -> int:
    return len(enc.encode(text))


def trim_to_token_budget(docs: list[dict], reserved_output_tokens: int = 512,
                         prompt_overhead_tokens: int = 150, safety_margin: int = 300) -> list[dict]:
    budget = max(MODEL_CONTEXT_WINDOW - reserved_output_tokens - prompt_overhead_tokens - safety_margin, 200)
    kept, total = [], 0
    for d in docs:
        t = count_tokens(d["text"])
        if kept and total + t > budget:
            break
        kept.append(d)
        total += t
    return kept


def adaptive_cutoff(reranked: list[dict], complexity: str = "MEDIUM", max_keep: int = 10) -> list[dict]:
    """Keep at least MIN_KEEP chunks, then stop at the first sharp relative drop in rerank_score.
    Relative drop uses |prev| as denominator because cross-encoder logits can be negative."""
    dropoff = DROPOFF_BY_COMPLEXITY.get(complexity, 0.12)
    min_keep = MIN_KEEP_BY_COMPLEXITY.get(complexity, 1)
    if len(reranked) <= min_keep:
        return reranked

    kept = [reranked[0]]
    for i in range(1, min(len(reranked), max_keep)):
        if len(kept) >= min_keep:
            prev, curr = reranked[i - 1]["rerank_score"], reranked[i]["rerank_score"]
            if (prev - curr) / max(abs(prev), 1e-6) > dropoff:
                break
        kept.append(reranked[i])
    return kept


def classify_complexity_heuristic(query: str) -> str | None:
    """Free heuristic. Returns None when unsure, which triggers the LLM classifier."""
    q = query.lower().strip()
    word_count = len(q.split())

    if any(s in q for s in ("trace", "end to end", "end-to-end", "architecture", "across", "interact")):
        return "HIGH"

    low_signals = ("what is", "where is", "define", "which file", "what does")
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
        if complexity not in RETRIEVAL_BUDGET:
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
    """Hybrid retrieval + rerank + adaptive cutoff + LLM generation.
    top_k: candidates to retrieve (None → chosen by query complexity). top_n: kept after rerank."""

    complexity, complexity_conf, complexity_reason, _classifier_info = classify_complexity(query)
    fetch_k = top_k or RETRIEVAL_BUDGET.get(complexity, RETRIEVAL_BUDGET["MEDIUM"])

    def result(answer, sources=(), confidence=0.0, n_chunks=0, context="", model_info=None):
        out = {
            "answer": answer, "sources": list(sources), "confidence": confidence,
            "complexity": complexity, "complexity_confidence": complexity_conf,
            "complexity_reason": complexity_reason, "retrieval_k": fetch_k,
            "final_chunk_count": n_chunks,
            "model_info": model_info or {"model_name": "none", "provider": "none", "paid": False},
        }
        if return_context:
            out["context"] = context
        return out

    # 1. Retrieve, drop weak fused hits (fused score normalised to [0, 1])
    bm25_weight, vector_weight, rrf_k = 0.4, 0.6, 60
    max_rrf = (bm25_weight + vector_weight) / (rrf_k + 1)
    docs = [d for d in hybrid_search.hybrid_retrieval(query, k=fetch_k)
            if d.get("fused_score", 0.0) / max_rrf >= min_score]
    if not docs:
        return result(ABSTENTION)

    # 2. Rerank, then adaptive cutoff
    if reranker is not None:
        docs = reranker.rerank(query, docs, top_n=(top_n or fetch_k))
    if use_adaptive and docs:
        docs = adaptive_cutoff(docs, complexity)
    if not docs:
        return result(ABSTENTION)

    # 3. Drop exact duplicates, fit the token budget
    seen, unique = set(), []
    for d in docs:
        key = (d["metadata"].get("file_path", "unknown"), d["text"].strip())
        if key not in seen:
            seen.add(key)
            unique.append(d)
    docs = trim_to_token_budget(unique, prompt_overhead_tokens=count_tokens(query) + 80,
                                reserved_output_tokens=512, safety_margin=200)
    if not docs:
        return result(ABSTENTION)

    context = "\n\n".join(d["text"] for d in docs).strip()
    sources = [{
        "source": d["metadata"].get("file_path", "unknown"),
        "page": d["metadata"].get("page", "unknown"),
        "score": d.get("fused_score", 0.0) / max_rrf,
        "preview": d["text"][:120] + "...",
    } for d in docs]
    confidence = max(s["score"] for s in sources)

    # 4. Abstain if even the best chunk is judged irrelevant, otherwise generate
    best_rerank_score = max((d.get("rerank_score", float("-inf")) for d in docs), default=float("-inf"))
    if best_rerank_score < RELEVANCE_THRESHOLD:
        return result(ABSTENTION, sources, confidence, len(docs), context)

    model_info = {"model_name": "none", "provider": "none", "paid": False}
    prompt = f"""Answer the question using only the repository context below.

Answer directly in 1–3 concise sentences.
Use relevant file names, functions, classes, endpoints, and code from the context.
Do not use outside knowledge.

Repository context:
{context}

Question:
{query}

Answer:"""
    try:
        gen_res = generation_router.invoke(prompt)
        raw_text = gen_res.content or ""
        clean_text = re.sub(r"<think>.*?</think>", "", raw_text, flags=re.DOTALL)
        clean_text = re.sub(r"<think>.*$", "", clean_text, flags=re.DOTALL)
        answer = clean_text.strip() or ABSTENTION
        model_info = {"model_name": gen_res.model_name, "provider": gen_res.provider, "paid": gen_res.paid}
    except Exception as exc:
        return result(f"Generation failed: {exc}", sources, confidence, len(docs), context, model_info)

    return result(answer, sources, confidence, len(docs), context, model_info)
