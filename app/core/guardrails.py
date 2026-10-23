"""
AI Guardrails — NVIDIA NeMo Guardrails
───────────────────────────────────────
Input rails  → topic restriction, jailbreak detection, PII blocking
Output rails → hallucination check, toxic-content filter, response safety

Uses a Colang configuration directory at config/guardrails/.
When guardrails are disabled (GUARDRAILS_ENABLED=false), the pass-through
functions simply return the original text.
"""

import logging
from pathlib import Path
from typing import Optional

from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

_rails = None


def _get_rails():
    """Lazy-load the NeMo Guardrails runtime from the config directory."""
    global _rails
    if _rails is not None:
        return _rails

    config_dir = Path(settings.guardrails_config_dir)
    if not config_dir.exists():
        logger.warning("Guardrails config directory not found at %s — guardrails disabled", config_dir)
        return None

    try:
        from nemoguardrails import RailsConfig, LLMRails

        config = RailsConfig.from_path(str(config_dir))
        _rails = LLMRails(config)
        logger.info("NeMo Guardrails loaded from %s", config_dir)
        return _rails
    except Exception as exc:
        logger.error("Failed to initialise NeMo Guardrails: %s", exc, exc_info=True)
        return None


async def check_input(user_message: str) -> dict:
    """
    Run input rails on the user message.
    Returns:
        {"allowed": True/False, "reason": str, "rewritten": str}
    """
    if not settings.guardrails_enabled:
        return {"allowed": True, "reason": "", "rewritten": user_message}

    rails = _get_rails()
    if rails is None:
        return {"allowed": True, "reason": "guardrails_unavailable", "rewritten": user_message}

    try:
        result = await rails.generate_async(
            messages=[{"role": "user", "content": user_message}]
        )
        response_content = result.get("content", "") if isinstance(result, dict) else str(result)

        # NeMo returns a refusal message if input rails block the request
        blocked_signals = [
            "I'm sorry",
            "I cannot",
            "not allowed",
            "outside my scope",
            "I can't help with that",
        ]
        is_blocked = any(sig.lower() in response_content.lower() for sig in blocked_signals)

        if is_blocked:
            logger.info("Input guardrail BLOCKED: %.100s", user_message)
            return {
                "allowed": False,
                "reason": "blocked_by_input_rail",
                "rewritten": user_message,
                "guardrail_message": response_content,
            }
        return {"allowed": True, "reason": "", "rewritten": user_message}

    except Exception as exc:
        logger.error("Input guardrail error: %s", exc, exc_info=True)
        # Fail open — allow the request if guardrails error
        return {"allowed": True, "reason": f"guardrail_error: {exc}", "rewritten": user_message}


async def check_output(user_message: str, assistant_response: str) -> dict:
    """
    Run output rails on the generated response.
    Returns:
        {"allowed": True/False, "reason": str, "filtered_response": str}
    """
    if not settings.guardrails_enabled:
        return {"allowed": True, "reason": "", "filtered_response": assistant_response}

    rails = _get_rails()
    if rails is None:
        return {"allowed": True, "reason": "guardrails_unavailable", "filtered_response": assistant_response}

    try:
        result = await rails.generate_async(
            messages=[
                {"role": "user", "content": user_message},
                {"role": "assistant", "content": assistant_response},
            ]
        )
        response_content = result.get("content", "") if isinstance(result, dict) else str(result)

        # If the output rail modifies/blocks the response, use the filtered version
        if response_content and response_content != assistant_response:
            logger.info("Output guardrail modified response")
            return {
                "allowed": True,
                "reason": "output_filtered",
                "filtered_response": response_content,
            }
        return {"allowed": True, "reason": "", "filtered_response": assistant_response}

    except Exception as exc:
        logger.error("Output guardrail error: %s", exc, exc_info=True)
        return {"allowed": True, "reason": f"guardrail_error: {exc}", "filtered_response": assistant_response}


# ─── Synchronous wrappers for non-async callers ─────────────────

def check_input_sync(user_message: str) -> dict:
    """Synchronous wrapper around check_input."""
    import asyncio
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                return pool.submit(
                    asyncio.run, check_input(user_message)
                ).result()
        return loop.run_until_complete(check_input(user_message))
    except RuntimeError:
        return asyncio.run(check_input(user_message))


def check_output_sync(user_message: str, assistant_response: str) -> dict:
    """Synchronous wrapper around check_output."""
    import asyncio
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                return pool.submit(
                    asyncio.run, check_output(user_message, assistant_response)
                ).result()
        return loop.run_until_complete(check_output(user_message, assistant_response))
    except RuntimeError:
        return asyncio.run(check_output(user_message, assistant_response))
