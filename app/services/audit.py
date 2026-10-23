"""
Enterprise Query & Trace Audit Service
──────────────────────────────────────
Records audit logs in PostgreSQL (via SQLAlchemy) with fallback to SQLite.
Captures user info, guardrail decisions, routing choices, citations, and traces.
"""

import json
import logging
from datetime import datetime, timezone
from typing import List, Optional

from app.core.config import get_settings
from app.db.init_db import init_database
from app.db.models import AuditLog
from app.db.session import get_session_factory

logger = logging.getLogger(__name__)
settings = get_settings()


def init_db() -> None:
    """Initialize database tables for audit logging and models."""
    init_database()


def write_audit(
    question: str,
    source_used: str,
    trace: List[str],
    username: Optional[str] = None,
    answer: Optional[str] = None,
    guardrail_blocked: bool = False,
    citations: Optional[List[str]] = None,
    execution_time_ms: Optional[int] = None,
) -> None:
    """
    Writes a query audit record to PostgreSQL (or SQLite fallback).
    """
    session_factory = get_session_factory()
    try:
        with session_factory() as session:
            record = AuditLog(
                created_at=datetime.now(timezone.utc),
                username=username,
                question=question,
                source_used=source_used,
                answer=answer,
                guardrail_blocked=guardrail_blocked,
                citations_json=json.dumps(citations or []),
                trace_json=json.dumps(trace or []),
                execution_time_ms=execution_time_ms,
            )
            session.add(record)
            session.commit()
    except Exception as exc:
        logger.error("Failed to write audit log: %s", exc)
