from __future__ import annotations

from typing import Literal

from src.config import Settings
from src.llm.anthropic_client import AnthropicClient
from src.llm.base import BaseLLMClient, LLMError
from src.llm.openai_client import OpenAIClient

ModelRole = Literal["planner", "response", "preprocessor"]


def make_llm_client(role: ModelRole, settings: Settings) -> BaseLLMClient:
    """Returns a configured client for the given role.

    The role -> model name mapping lives in Settings so it can be tuned per
    deployment. Provider is picked from `LLM_PROVIDER`.
    """
    provider = settings.LLM_PROVIDER

    if provider == "anthropic":
        model = {
            "planner": settings.MODEL_PLANNER,
            "response": settings.MODEL_RESPONSE,
            "preprocessor": settings.MODEL_PREPROCESSOR,
        }[role]
        return AnthropicClient(model=model, api_key=settings.ANTHROPIC_API_KEY)

    if provider == "openai":
        model = {
            "planner": settings.OPENAI_MODEL_PLANNER,
            "response": settings.OPENAI_MODEL_RESPONSE,
            "preprocessor": settings.OPENAI_MODEL_PREPROCESSOR,
        }[role]
        return OpenAIClient(model=model, api_key=settings.OPENAI_API_KEY)

    raise LLMError(f"Unknown LLM_PROVIDER: {provider!r}")
