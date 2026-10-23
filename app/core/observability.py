"""
AI Observability — LangSmith Tracing & Telemetry
────────────────────────────────────────────────
Integrates with:
  • LangSmith (primary AI Observability & evaluation platform)

Provides:
  • Automatic tracing of all LLM calls, retrieval, and LangGraph decisions
  • Latency, token usage, and cost tracking
  • User and session level run tagging
  • Request metrics collection and tracing callbacks
"""

import logging
import os
import time
import uuid
from contextlib import contextmanager
from typing import Any, List, Optional

from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

_initialized: bool = False
_langsmith_client = None
_langsmith_tracer = None


def init_observability() -> None:
    """
    Initialise LangSmith observability at app startup.
    Sets standard LangChain environment variables and configures the LangSmith client.
    """
    global _initialized, _langsmith_client, _langsmith_tracer
    if _initialized:
        return

    if not settings.observability_enabled:
        logger.info("Observability is disabled by configuration.")
        _initialized = True
        return

    # ── LangSmith ─────────────────────────────────────────────
    if settings.langsmith_api_key:
        os.environ["LANGCHAIN_TRACING_V2"] = "true" if settings.langsmith_tracing else "false"
        os.environ["LANGCHAIN_API_KEY"] = settings.langsmith_api_key
        os.environ["LANGCHAIN_PROJECT"] = settings.langsmith_project
        os.environ["LANGCHAIN_ENDPOINT"] = settings.langsmith_endpoint

        try:
            from langsmith import Client
            from langchain_core.tracers import LangChainTracer

            _langsmith_client = Client(
                api_key=settings.langsmith_api_key,
                api_url=settings.langsmith_endpoint,
            )
            _langsmith_tracer = LangChainTracer(project_name=settings.langsmith_project)
            logger.info(
                " LangSmith observability initialised (project='%s', endpoint='%s')",
                settings.langsmith_project,
                settings.langsmith_endpoint,
            )
        except Exception as exc:
            logger.warning("LangSmith client initialization notice: %s", exc)
    else:
        logger.info("LangSmith API key not configured. Tracing operating in local passive mode.")

    _initialized = True


def is_langsmith_active() -> bool:
    """Returns True if LangSmith tracing is configured and active."""
    return bool(settings.langsmith_api_key and settings.observability_enabled)


def get_langsmith_client():
    """Return active LangSmith client (or None)."""
    return _langsmith_client


def get_callbacks() -> List[Any]:
    """
    Return a list of LangChain callback handlers for the current request.
    Attach these to LangChain / LangGraph invocations.
    """
    callbacks = []
    if _langsmith_tracer is not None:
        callbacks.append(_langsmith_tracer)
    return callbacks


# ───────────────────────────── Request Metrics ────────────────────

class RequestMetrics:
    """Tracks timing and token metrics for a single request."""

    def __init__(self, question: str, session_id: Optional[str] = None):
        self.trace_id = str(uuid.uuid4())
        self.session_id = session_id or str(uuid.uuid4())
        self.question = question
        self.start_time = time.monotonic()
        self.events: List[dict[str, Any]] = []
        self._step_start: Optional[float] = None

    def start_step(self, step_name: str) -> None:
        self._step_start = time.monotonic()
        self.events.append({
            "step": step_name,
            "started_at": time.time(),
        })

    def end_step(self, step_name: str, metadata: Optional[dict] = None) -> None:
        elapsed = (time.monotonic() - self._step_start) if self._step_start else 0
        for evt in reversed(self.events):
            if evt["step"] == step_name and "duration_ms" not in evt:
                evt["duration_ms"] = round(elapsed * 1000, 2)
                if metadata:
                    evt["metadata"] = metadata
                break

    def finish(self) -> dict[str, Any]:
        total_ms = round((time.monotonic() - self.start_time) * 1000, 2)
        summary = {
            "trace_id": self.trace_id,
            "session_id": self.session_id,
            "question": self.question,
            "total_duration_ms": total_ms,
            "steps": self.events,
            "langsmith_project": settings.langsmith_project if is_langsmith_active() else None,
        }
        return summary


@contextmanager
def observe_request(question: str, session_id: Optional[str] = None):
    """
    Context manager that creates a RequestMetrics object,
    yields it for step tracking, and finalises on exit.
    """
    metrics = RequestMetrics(question, session_id)
    try:
        yield metrics
    finally:
        metrics.finish()
