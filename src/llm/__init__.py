"""
LLM Module - Multi-Provider Support

Provides unified interface for multiple LLM providers:
- OpenAI (GPT-4, GPT-3.5)
- Ollama (self-hosted, Vietnamese-optimized)
- Azure OpenAI

Default: MultiProviderLLMClient (supports Ollama for Vietnamese)
"""

# Re-export legacy classes for backward compatibility
from .client import LLMClient as _LegacyLLMClient
from .client import LLMError as _LegacyLLMError
from .client import get_llm as _legacy_get_llm

# Re-export V2 classes
from .client_v2 import EnhancedLLMClient
from .client_v2 import get_llm_client as get_llm_client_v2
from .client_v2 import LLMResponse as LLMResponseV2
from .client_v2 import LLMError as LLMErrorV2

# New multi-provider client (recommended)
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

# Create the default singleton - use MultiProviderLLMClient
# This is what gets imported when you do: from src.llm import llm_client
llm_client = MultiProviderLLMClient()


# Backward compatibility - deprecated, use MultiProviderLLMClient
def get_llm():
    """Deprecated: Use get_llm_client() instead"""
    import warnings
    warnings.warn(
        "get_llm() is deprecated. Use get_llm_client() instead.",
        DeprecationWarning,
        stacklevel=2
    )
    return llm_client


__all__ = [
    # Default export (recommended)
    "MultiProviderLLMClient",
    "llm_client",
    "get_llm_client",
    
    # Legacy exports (deprecated)
    "LLMClient",
    "get_llm",
    "LLMError",
    
    # V2 exports
    "EnhancedLLMClient",
    "get_llm_client_v2",
    "LLMResponseV2",
    "LLMErrorV2",
    
    # Provider enums and types
    "LLMProvider",
    "LLMResponse",
    "LLMProviderError",
    "LLMTimeoutError",
    "LLMParseError",
    "VIETNAMESE_MODELS",
]

# Alias for backward compatibility
LLMClient = MultiProviderLLMClient
