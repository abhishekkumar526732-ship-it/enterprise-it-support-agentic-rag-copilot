"""
RAG Evaluation — Ragas
──────────────────────
Provides endpoints to evaluate the quality of RAG pipeline responses using
standard Ragas metrics:

  • Faithfulness   — Is the answer grounded in the retrieved context?
  • Answer Relevancy — Is the answer relevant to the question?
  • Context Precision — Are the retrieved docs relevant and well-ranked?
  • Context Recall — Did retrieval capture all needed information?

POST /api/evaluate/single   → Evaluate a single Q&A pair
POST /api/evaluate/batch    → Evaluate a batch of test cases
GET  /api/evaluate/health   → Check Ragas availability
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.core.security import TokenPayload, require_analyst

logger = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter(prefix="/api/evaluate", tags=["evaluation"])


# ───────────────────────────── Request / Response models ─────────

class EvalSingleRequest(BaseModel):
    question: str = Field(min_length=2, description="The user question")
    answer: str = Field(min_length=1, description="The generated answer")
    contexts: list[str] = Field(default_factory=list, description="Retrieved context chunks")
    ground_truth: Optional[str] = Field(default=None, description="Expected correct answer (for recall)")


class EvalBatchRequest(BaseModel):
    test_cases: list[EvalSingleRequest] = Field(min_length=1, max_length=100)


class EvalScores(BaseModel):
    faithfulness: Optional[float] = None
    answer_relevancy: Optional[float] = None
    context_precision: Optional[float] = None
    context_recall: Optional[float] = None


class EvalSingleResponse(BaseModel):
    question: str
    scores: EvalScores
    status: str = "ok"


class EvalBatchResponse(BaseModel):
    results: list[EvalSingleResponse]
    aggregate: EvalScores
    total: int
    status: str = "ok"


# ───────────────────────────── Ragas evaluation logic ────────────

def _run_ragas_evaluation(test_cases: list[EvalSingleRequest]) -> list[dict]:
    """
    Run Ragas evaluation on a list of test cases.
    Returns a list of score dictionaries.
    """
    try:
        from datasets import Dataset
        from ragas import evaluate
        from ragas.metrics import (
            answer_relevancy,
            context_precision,
            context_recall,
            faithfulness,
        )

        # Build the dataset in Ragas-expected format
        data = {
            "question": [],
            "answer": [],
            "contexts": [],
            "ground_truth": [],
        }

        for tc in test_cases:
            data["question"].append(tc.question)
            data["answer"].append(tc.answer)
            data["contexts"].append(tc.contexts if tc.contexts else ["No context available."])
            data["ground_truth"].append(tc.ground_truth or "")

        dataset = Dataset.from_dict(data)

        # Select metrics based on available data
        metrics = [faithfulness, answer_relevancy, context_precision]
        has_ground_truth = any(tc.ground_truth for tc in test_cases)
        if has_ground_truth:
            metrics.append(context_recall)

        result = evaluate(dataset, metrics=metrics)

        # Convert to list of score dicts
        scores_list = []
        result_df = result.to_pandas()
        for _, row in result_df.iterrows():
            scores_list.append({
                "faithfulness": float(row.get("faithfulness", 0)) if "faithfulness" in row else None,
                "answer_relevancy": float(row.get("answer_relevancy", 0)) if "answer_relevancy" in row else None,
                "context_precision": float(row.get("context_precision", 0)) if "context_precision" in row else None,
                "context_recall": float(row.get("context_recall", 0)) if "context_recall" in row else None,
            })

        return scores_list

    except ImportError as exc:
        logger.error("Ragas not installed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Ragas evaluation unavailable: {exc}")
    except Exception as exc:
        logger.error("Ragas evaluation failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Evaluation error: {exc}")


def _aggregate_scores(scores_list: list[dict]) -> EvalScores:
    """Compute the mean of each metric across all test cases."""
    if not scores_list:
        return EvalScores()

    agg = {}
    for key in ["faithfulness", "answer_relevancy", "context_precision", "context_recall"]:
        values = [s[key] for s in scores_list if s.get(key) is not None]
        agg[key] = round(sum(values) / len(values), 4) if values else None

    return EvalScores(**agg)


# ───────────────────────────── Endpoints ─────────────────────────

@router.get("/health")
def eval_health():
    """Check if Ragas is available."""
    try:
        import ragas
        return {"status": "ok", "ragas_version": ragas.__version__, "enabled": settings.ragas_enabled}
    except ImportError:
        return {"status": "unavailable", "ragas_version": None, "enabled": settings.ragas_enabled}


@router.post("/single", response_model=EvalSingleResponse)
def evaluate_single(
    payload: EvalSingleRequest,
    _user: TokenPayload = Depends(require_analyst),
):
    """Evaluate a single question-answer pair using Ragas metrics and log to DB."""
    if not settings.ragas_enabled:
        raise HTTPException(status_code=400, detail="RAG evaluation is disabled")

    scores_list = _run_ragas_evaluation([payload])
    scores = EvalScores(**(scores_list[0] if scores_list else {}))

    # Persist evaluation result to PostgreSQL / DB
    try:
        import json
        from app.db.session import get_session_factory
        from app.db.models import EvaluationRun
        session_factory = get_session_factory()
        with session_factory() as session:
            run = EvaluationRun(
                evaluated_by=_user.sub,
                question=payload.question,
                faithfulness=scores.faithfulness,
                answer_relevancy=scores.answer_relevancy,
                context_precision=scores.context_precision,
                context_recall=scores.context_recall,
                status="completed",
                metrics_json=json.dumps(scores.model_dump()),
            )
            session.add(run)
            session.commit()
    except Exception as exc:
        logger.debug("Could not persist evaluation run to database: %s", exc)

    return EvalSingleResponse(
        question=payload.question,
        scores=scores,
    )


@router.post("/batch", response_model=EvalBatchResponse)
def evaluate_batch(
    payload: EvalBatchRequest,
    _user: TokenPayload = Depends(require_analyst),
):
    """Evaluate a batch of test cases using Ragas metrics."""
    if not settings.ragas_enabled:
        raise HTTPException(status_code=400, detail="RAG evaluation is disabled")

    scores_list = _run_ragas_evaluation(payload.test_cases)

    results = []
    for tc, scores in zip(payload.test_cases, scores_list):
        results.append(EvalSingleResponse(
            question=tc.question,
            scores=EvalScores(**scores),
        ))

    aggregate = _aggregate_scores(scores_list)

    # Persist batch evaluations to DB
    try:
        import json
        from app.db.session import get_session_factory
        from app.db.models import EvaluationRun
        session_factory = get_session_factory()
        with session_factory() as session:
            for tc, scores in zip(payload.test_cases, scores_list):
                session.add(
                    EvaluationRun(
                        evaluated_by=_user.sub,
                        question=tc.question,
                        faithfulness=scores.get("faithfulness"),
                        answer_relevancy=scores.get("answer_relevancy"),
                        context_precision=scores.get("context_precision"),
                        context_recall=scores.get("context_recall"),
                        status="completed",
                        metrics_json=json.dumps(scores),
                    )
                )
            session.commit()
    except Exception as exc:
        logger.debug("Could not persist batch evaluation runs to database: %s", exc)

    return EvalBatchResponse(
        results=results,
        aggregate=aggregate,
        total=len(results),
    )


@router.get("/history")
def evaluation_history(
    limit: int = 50,
    _user: TokenPayload = Depends(require_analyst),
):
    """Fetch past RAG evaluation records from PostgreSQL (requires analyst+ role)."""
    try:
        from app.db.session import get_session_factory
        from app.db.models import EvaluationRun
        session_factory = get_session_factory()
        with session_factory() as session:
            runs = (
                session.query(EvaluationRun)
                .order_by(EvaluationRun.created_at.desc())
                .limit(limit)
                .all()
            )
            return [
                {
                    "id": r.id,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                    "evaluated_by": r.evaluated_by,
                    "question": r.question,
                    "faithfulness": r.faithfulness,
                    "answer_relevancy": r.answer_relevancy,
                    "context_precision": r.context_precision,
                    "context_recall": r.context_recall,
                    "status": r.status,
                }
                for r in runs
            ]
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to fetch evaluation history: {exc}")

