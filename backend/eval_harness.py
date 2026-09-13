"""
LLM-as-a-Judge Evaluation Harness

Evaluates answer quality (0.0 to 1.0) for both adaptive and baseline answers.
Supports fast keyword-based abstention detection and asynchronous background scoring.
"""
import os
import re
import logging
import threading
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Abstention phrases indicating the model correctly abstained from answering
ABSTENTION_PHRASES = [
    "cannot be determined",
    "does not contain",
    "no information",
    "not mentioned",
    "not provided",
    "i couldn't find",
    "i could not find",
    "insufficient context",
    "no context provided",
    "not specified in the context",
    "context does not provide",
]

JUDGE_PROMPT_TEMPLATE = """You are an expert code quality evaluator.
Evaluate the accuracy and completeness of the ANSWER given the QUESTION and CONTEXT from a software repository.

QUESTION: {query}

CONTEXT:
{context}

ANSWER: {answer}

Score scale:
1.0  = Fully correct, accurate, and directly answers the question based on context.
0.75 = Mostly correct, minor details missing or slight phrasing issues.
0.50 = Partially correct, missing key details or contains minor inaccuracies.
0.25 = Mostly incorrect, vague, or misinterprets the context.
0.0  = Completely incorrect, irrelevant, or hallucinated.

Respond with ONLY a single numeric score (0.0, 0.25, 0.5, 0.75, or 1.0). Do not include any explanations or extra text."""


def is_abstention(answer: str) -> bool:
    """Return True if the answer indicates abstention due to lack of context."""
    if not answer:
        return True
    answer_lower = answer.lower()
    return any(phrase in answer_lower for phrase in ABSTENTION_PHRASES)


def judge_answer(query: str, answer: str, context: str, llm: Any = None) -> float:
    """Use an LLM judge to evaluate answer accuracy against context."""
    if not answer:
        return 0.0

    # Fast-path for abstentions
    if is_abstention(answer):
        return 1.0

    prompt = JUDGE_PROMPT_TEMPLATE.format(
        query=query,
        context=context if context else "No context provided.",
        answer=answer[:2000],
    )

    try:
        if llm is not None:
            if hasattr(llm, "invoke"):
                response = llm.invoke(prompt)
                print(f"[DEBUG] raw judge response: {response.content!r}")
                res_text = getattr(response, "content", str(response)).strip()
            else:
                res_text = str(llm(prompt)).strip()
        else:
            from llm_router import judge_router
            res = judge_router.invoke(prompt)
            baseline_judge_response = res.content
            print(f"[DEBUG-LIVE] raw judge response (baseline): {baseline_judge_response!r}")
            res_text = baseline_judge_response

        match = re.search(r"(1\.0|0\.75|0\.50|0\.5|0\.25|0\.0|0)", res_text)
        if match:
            return float(match.group(1))
        logger.warning(
            "Judge LLM returned unparseable response (falling back to 0.5). Raw: %r", res_text
        )
        return 0.5
    except Exception as e:
        logger.error("Judge LLM execution failed (falling back to 0.5): %s", e)
        return 0.5



def score_query_async(
    entry: dict[str, Any],
    adaptive_context: str,
    baseline_context: Optional[str] = None,
    llm: Any = None,
) -> None:
    """Asynchronously evaluate log entry scores in a background thread."""
    def _worker():
        from logging_utils import update_query_log_scores
        try:
            query = entry["query"]
            adaptive_ans = entry.get("adaptive_answer", "")
            baseline_ans = entry.get("baseline_answer")

            # Judge adaptive answer
            adaptive_score = judge_answer(query, adaptive_ans, adaptive_context, llm=llm)

            # Judge baseline answer if present.
            # Use baseline_context explicitly (even if empty string) — don't
            # fall back to adaptive_context or both scores will be identical.
            baseline_score = None
            if baseline_ans:
                print(f"[DEBUG-LIVE] baseline_ctx is None: {baseline_context is None}, len={len(baseline_context) if baseline_context else 'N/A'}")
                print(f"[DEBUG-LIVE] adaptive_ctx len={len(adaptive_context)}")
                ctx = baseline_context if baseline_context is not None else adaptive_context
                baseline_score = judge_answer(query, baseline_ans, ctx, llm=llm)

            update_query_log_scores(
                timestamp=entry["timestamp"],
                adaptive_score=adaptive_score,
                baseline_score=baseline_score,
                eval_status="done",
            )
        except Exception as e:
            logger.error(f"Background score evaluation failed: {e}")
            from logging_utils import update_query_log_scores
            update_query_log_scores(
                timestamp=entry["timestamp"],
                adaptive_score=None,
                baseline_score=None,
                eval_status="error",
            )

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
