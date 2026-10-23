"""
LLM Gateway — LiteLLM
──────────────────────
Routes all LLM calls through LiteLLM, enabling:
  • Multi-provider support (Google Gemini, OpenAI, Anthropic, etc.)
  • Automatic fallback chain
  • Spend tracking, rate-limiting hooks
  • Unified API across providers

Default model: gemini/gemini-3.8-flash  (Google Gemini 3.8 Flash)
Fallback:      gpt-4o-mini              (OpenAI)
"""

import logging
from typing import Any, Optional

import litellm
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# ───────────────────────────── LiteLLM global config ─────────────
litellm.drop_params = True          # silently drop unsupported params per provider
litellm.set_verbose = False

# Register API keys so LiteLLM can route to the right provider
if settings.gemini_api_key:
    litellm.api_key = settings.gemini_api_key                        # default key
    import os
    os.environ.setdefault("GEMINI_API_KEY", settings.gemini_api_key)
if settings.openai_api_key:
    import os
    os.environ.setdefault("OPENAI_API_KEY", settings.openai_api_key)

# ───────────────────────────── Router config ─────────────────────

MODEL_LIST = [
    {
        "model_name": "primary",
        "litellm_params": {
            "model": settings.litellm_model,               # gemini/gemini-3.8-flash
            "api_key": settings.gemini_api_key or None,
            "timeout": settings.litellm_timeout,
        },
    },
    {
        "model_name": "primary",
        "litellm_params": {
            "model": settings.litellm_fallback_model,       # gpt-4o-mini  (fallback)
            "api_key": settings.openai_api_key or None,
            "timeout": settings.litellm_timeout,
        },
    },
]

_router: Optional[litellm.Router] = None


def get_router() -> litellm.Router:
    """Lazy-initialised LiteLLM router with fallback chain."""
    global _router
    if _router is None:
        valid_models = [
            m for m in MODEL_LIST
            if m["litellm_params"].get("api_key")
        ]
        if not valid_models:
            raise RuntimeError(
                "No LLM API key configured. Set GEMINI_API_KEY or OPENAI_API_KEY."
            )
        _router = litellm.Router(
            model_list=valid_models,
            routing_strategy="simple-shuffle",
            num_retries=2,
            retry_after=5,
            fallbacks=[{"primary": ["primary"]}],
        )
        logger.info(
            "LiteLLM Router initialised — models: %s",
            [m["litellm_params"]["model"] for m in valid_models],
        )
    return _router


# ───────────────────────────── simple completion helper ──────────

def gateway_completion(
    prompt: str,
    *,
    system: str = "",
    temperature: float | None = None,
    max_tokens: int | None = None,
    response_format: dict | None = None,
) -> str:
    """
    Single-turn completion through the LiteLLM gateway.
    Returns the raw text content of the assistant reply.
    """
    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    kwargs: dict[str, Any] = {
        "model": "primary",
        "messages": messages,
        "temperature": temperature if temperature is not None else settings.litellm_temperature,
        "max_tokens": max_tokens or settings.litellm_max_tokens,
    }
    if response_format:
        kwargs["response_format"] = response_format

    router = get_router()
    response = router.completion(**kwargs)
    content = response.choices[0].message.content
    logger.debug("LiteLLM gateway response (model=%s): %.120s…", response.model, content)
    return content


# ───────────────────────────── LangChain ChatModel wrapper ───────

class ChatLiteLLMGateway(BaseChatModel):
    """
    LangChain-compatible ChatModel backed by our LiteLLM Router.
    Drop-in replacement for ChatOpenAI in the LangGraph workflow.
    """
    model_name: str = Field(default="primary")
    temperature: float = Field(default=0.0)
    max_tokens: int = Field(default=4096)

    class Config:
        arbitrary_types_allowed = True

    @property
    def _llm_type(self) -> str:
        return "litellm-gateway"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: Optional[list[str]] = None,
        **kwargs: Any,
    ) -> ChatResult:
        litellm_messages = []
        for msg in messages:
            if isinstance(msg, SystemMessage):
                litellm_messages.append({"role": "system", "content": msg.content})
            elif isinstance(msg, HumanMessage):
                litellm_messages.append({"role": "user", "content": msg.content})
            elif isinstance(msg, AIMessage):
                litellm_messages.append({"role": "assistant", "content": msg.content})
            else:
                litellm_messages.append({"role": "user", "content": msg.content})

        call_kwargs: dict[str, Any] = {
            "model": self.model_name,
            "messages": litellm_messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if stop:
            call_kwargs["stop"] = stop

        # pass through any extra kwargs (e.g. response_format)
        call_kwargs.update(kwargs)

        router = get_router()
        response = router.completion(**call_kwargs)
        content = response.choices[0].message.content or ""

        return ChatResult(
            generations=[ChatGeneration(message=AIMessage(content=content))],
        )


# ───────────────────────────── factory ───────────────────────────

_chat_model: Optional[ChatLiteLLMGateway] = None


def get_chat_model() -> ChatLiteLLMGateway:
    """Return the singleton LangChain ChatModel backed by LiteLLM."""
    global _chat_model
    if _chat_model is None:
        _chat_model = ChatLiteLLMGateway(
            model_name="primary",
            temperature=settings.litellm_temperature,
            max_tokens=settings.litellm_max_tokens,
        )
    return _chat_model
