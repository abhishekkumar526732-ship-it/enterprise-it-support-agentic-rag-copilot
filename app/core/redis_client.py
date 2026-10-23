"""
Redis Client & Resilient In-Memory Fallback
───────────────────────────────────────────
Provides:
  • Redis connection pooling with fast socket timeouts
  • Safe availability probing (no startup hangs if Redis is not running)
  • Automatic in-memory TTL dictionary fallback
"""

import logging
import time
from typing import Any, Optional

from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

_redis_client = None
_is_redis_active: bool = False
_in_memory_store: dict[str, tuple[float, Any]] = {}  # key -> (expiry_monotonic, value)


def _init_redis():
    """Attempt connection to Redis server. Fallback to memory store if unreachable."""
    global _redis_client, _is_redis_active

    if not settings.redis_enabled:
        logger.info("Redis is disabled by configuration. Operating in in-memory mode.")
        _is_redis_active = False
        return None

    try:
        import redis

        client = redis.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            password=settings.redis_password or None,
            db=settings.redis_db,
            socket_connect_timeout=settings.redis_connect_timeout,
            socket_timeout=settings.redis_connect_timeout,
            decode_responses=True,
        )
        # Fast ping test
        client.ping()
        _redis_client = client
        _is_redis_active = True
        logger.info(" Connected to Redis cache at %s:%s (db=%s)",
                    settings.redis_host, settings.redis_port, settings.redis_db)
        return _redis_client
    except Exception as exc:
        _is_redis_active = False
        logger.warning(
            " Redis is not reachable (%s). Falling back to in-memory TTL cache.",
            str(exc).split("\n")[0]
        )
        return None


def get_redis_client():
    """Returns active Redis client if available, else None."""
    global _redis_client
    if _redis_client is None and _is_redis_active:
        return _redis_client
    if _redis_client is None:
        _init_redis()
    return _redis_client


def is_redis_available() -> bool:
    """Check whether Redis is connected and responsive."""
    global _is_redis_active
    get_redis_client()
    return _is_redis_active


def cache_get(key: str) -> Optional[str]:
    """Retrieve string or JSON from Redis, with memory fallback."""
    client = get_redis_client()
    if client and _is_redis_active:
        try:
            return client.get(key)
        except Exception as exc:
            logger.debug("Redis GET error (%s), checking memory fallback: %s", key, exc)

    # In-memory fallback
    item = _in_memory_store.get(key)
    if item:
        exp, val = item
        if exp > time.monotonic():
            return val
        else:
            del _in_memory_store[key]
    return None


def cache_set(key: str, value: str, ttl_seconds: Optional[int] = None) -> bool:
    """Store string or JSON in Redis with TTL, with memory fallback."""
    ttl = ttl_seconds if ttl_seconds is not None else settings.redis_cache_ttl
    client = get_redis_client()
    if client and _is_redis_active:
        try:
            client.set(key, value, ex=ttl)
            return True
        except Exception as exc:
            logger.debug("Redis SET error (%s), saving to memory store: %s", key, exc)

    # In-memory fallback
    _in_memory_store[key] = (time.monotonic() + ttl, value)
    return True


def cache_delete(key: str) -> bool:
    """Delete a key from Redis / memory store."""
    client = get_redis_client()
    if client and _is_redis_active:
        try:
            client.delete(key)
        except Exception:
            pass
    _in_memory_store.pop(key, None)
    return True
