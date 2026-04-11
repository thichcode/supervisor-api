"""
LLM Module - Multi-Provider Support

Provides unified interface for multiple LLM providers:
- OpenAI (GPT-4, GPT-3.5)
- Ollama (self-hosted, Vietnamese-optimized)
- Azure OpenAI

Default: MultiProviderLLMClient (supports Ollama for Vietnamese)
"""

from .provider import (
    MultiProviderLLMClient,
    get_llm_client,
    LLMProvider,
    LLMResponse,
    LLMError,
    LLMProviderError,
    LLMTimeoutError,
    LLMParseError,
    VIETNAMESE_MODELS,
)

llm_client = MultiProviderLLMClient()


def get_llm():
    """Get the default LLM client."""
    return llm_client


__all__ = [
    "MultiProviderLLMClient",
    "llm_client",
    "get_llm_client",
    "get_llm",
    "LLMProvider",
    "LLMResponse",
    "LLMError",
    "LLMProviderError",
    "LLMTimeoutError",
    "LLMParseError",
    "VIETNAMESE_MODELS",
]