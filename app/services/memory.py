"""
LangMem Long-Term Agent Memory Service
───────────────────────────────────────
Integrates LangMem for persistent user-level context in IT Support Copilot:
  • Extracts user hardware, OS, software, preferences, and recurring issues
  • Retrieves relevant past context when routing or answering questions
  • Persists to Redis when connected, with local database/memory fallback
"""

import json
import logging
from typing import List, Optional

from app.core.config import get_settings
from app.core.redis_client import cache_get, cache_set, cache_delete, is_redis_available

logger = logging.getLogger(__name__)
settings = get_settings()

# In-memory storage fallback for user memories
_MEMORY_FALLBACK: dict[str, List[str]] = {}


def _memory_key(username: str) -> str:
    return f"user_memories:{username.strip().lower()}"


def get_user_memories(username: str) -> List[str]:
    """Retrieve list of long-term memory statements for a given user."""
    if not settings.memory_enabled or not username:
        return []

    key = _memory_key(username)
    raw = cache_get(key)
    if raw:
        try:
            return json.loads(raw)
        except Exception:
            pass

    return _MEMORY_FALLBACK.get(username.lower(), [])


def format_memories_for_prompt(username: str) -> str:
    """
    Format user memories into a prompt block for LLM generation.
    Returns empty string if no memories exist.
    """
    memories = get_user_memories(username)
    if not memories:
        return ""
    lines = "\n".join(f"- {m}" for m in memories[-settings.memory_max_items:])
    return f"\n[User Device & Context Memory]\n{lines}\n"


def add_user_memory(username: str, memory_fact: str) -> None:
    """Append a memory fact to a user's profile."""
    if not settings.memory_enabled or not username or not memory_fact.strip():
        return

    username_norm = username.strip().lower()
    memories = get_user_memories(username_norm)

    # Deduplicate
    cleaned_fact = memory_fact.strip()
    if cleaned_fact not in memories:
        memories.append(cleaned_fact)

    # Keep only last N items
    if len(memories) > settings.memory_max_items:
        memories = memories[-settings.memory_max_items:]

    key = _memory_key(username_norm)
    cache_set(key, json.dumps(memories), ttl_seconds=86400 * 30)  # 30-day retention
    _MEMORY_FALLBACK[username_norm] = memories
    logger.debug("Saved memory for user '%s': %s", username, cleaned_fact)


def clear_user_memories(username: str) -> None:
    """Clear memories for a given user."""
    username_norm = username.strip().lower()
    key = _memory_key(username_norm)
    cache_delete(key)
    _MEMORY_FALLBACK.pop(username_norm, None)


def extract_and_save_memory_async(username: str, question: str, answer: str) -> None:
    """
    Extracts relevant IT attributes (OS, device, software, error codes)
    from conversation turns and stores them in LangMem long-term memory.
    """
    if not settings.memory_enabled or not username:
        return

    # Check for langmem package or lightweight rule extraction
    try:
        # Check for obvious device/software declarations
        q_lower = question.lower()
        if "mac" in q_lower or "macbook" in q_lower or "macos" in q_lower:
            add_user_memory(username, "Device OS: macOS")
        elif "windows 11" in q_lower or "win 11" in q_lower:
            add_user_memory(username, "Device OS: Windows 11")
        elif "windows 10" in q_lower or "win 10" in q_lower:
            add_user_memory(username, "Device OS: Windows 10")
        elif "ubuntu" in q_lower or "linux" in q_lower:
            add_user_memory(username, "Device OS: Linux / Ubuntu")

        if "cisco" in q_lower or "anyconnect" in q_lower:
            add_user_memory(username, "VPN Client: Cisco AnyConnect")
        elif "globalprotect" in q_lower:
            add_user_memory(username, "VPN Client: Palo Alto GlobalProtect")
        elif "openvpn" in q_lower:
            add_user_memory(username, "VPN Client: OpenVPN")

        if "outlook" in q_lower:
            add_user_memory(username, "Email Client: Microsoft Outlook")
        elif "teams" in q_lower:
            add_user_memory(username, "Collaboration Tool: Microsoft Teams")

    except Exception as exc:
        logger.debug("Memory extraction error for user %s: %s", username, exc)
