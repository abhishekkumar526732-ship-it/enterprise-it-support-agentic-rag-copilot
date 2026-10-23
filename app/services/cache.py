"""
Query Caching Service — Redis & Memory
──────────────────────────────────────
Caches frequent IT support questions and grounded answers to minimize
latency and LLM cost.
"""

import hashlib
import json
import logging
from typing import Any, Optional

from app.core.redis_client import cache_get, cache_set, cache_delete, is_redis_available

logger = logging.getLogger(__name__)


def _hash_query(question: str) -> str:
    """Generate normalized cache key for a question."""
    normalized = " ".join(question.strip().lower().split())
    return "query_cache:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def get_cached_response(question: str) -> Optional[dict]:
    """Retrieve cached query response if available."""
    key = _hash_query(question)
    raw = cache_get(key)
    if raw:
        try:
            data = json.loads(raw)
            data["cached"] = True
            data["cache_backend"] = "redis" if is_redis_available() else "memory"
            logger.debug("Cache hit for question: '%s'", question[:40])
            return data
        except Exception:
            return None
    return None


def set_cached_response(question: str, response_data: dict, ttl_seconds: Optional[int] = None) -> None:
    """Store query response in cache."""
    # Don't cache error responses or blocked guardrails
    if response_data.get("guardrail_blocked"):
        return
    key = _hash_query(question)
    try:
        # Clone and remove any non-serializable fields
        to_store = {
            "answer": response_data.get("answer"),
            "source_used": response_data.get("source_used"),
            "trace": response_data.get("trace", []),
            "citations": response_data.get("citations", []),
            "rewritten_query": response_data.get("rewritten_query"),
            "guardrail_blocked": False,
        }
        cache_set(key, json.dumps(to_store), ttl_seconds=ttl_seconds)
        logger.debug("Cached response for question: '%s'", question[:40])
    except Exception as exc:
        logger.warning("Failed to serialize cache entry: %s", exc)


def invalidate_query_cache(question: str) -> None:
    """Invalidate cache entry for a query."""
    key = _hash_query(question)
    cache_delete(key)
