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
        context=context[:4000] if context else "No context provided.",
        answer=answer[:2000],
    )

    try:
        if llm is not None:
            if hasattr(llm, "invoke"):
                response = llm.invoke(prompt)
                res_text = getattr(response, "content", str(response)).strip()
            else:
                res_text = str(llm(prompt)).strip()
        else:
            from llm_router import judge_router
            res = judge_router.invoke(prompt)
            res_text = res.content

        match = re.search(r"(1\.0|0\.75|0\.50|0\.5|0\.25|0\.0|0)", res_text)
        if match:
            return float(match.group(1))
        return 0.5
    except Exception as e:
        logger.error(f"Error during LLM judge execution: {e}")
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

            # Judge baseline answer if present
            baseline_score = None
            if baseline_ans:
                ctx = baseline_context or adaptive_context
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
