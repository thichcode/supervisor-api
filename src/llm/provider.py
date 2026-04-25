"""
Multi-Provider LLM Client
Supports: OpenAI, Ollama, Azure OpenAI
Optimized for Vietnamese language processing
"""
from openai import AsyncOpenAI
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)
from typing import Optional, TypeVar, Type, List, Any, Dict
from dataclasses import dataclass, field
from enum import Enum
import structlog
import json
import httpx

from src.config import get_settings
from src.core.circuit_breaker import (
    get_circuit_breaker,
    CircuitBreakerConfig,
    CircuitBreakerError,
    CircuitBreakerMetrics,
    CircuitState,
)

settings = get_settings()
logger = structlog.get_logger()

T = TypeVar('T')


class LLMProvider(Enum):
    """Supported LLM providers"""
    OPENAI = "openai"
    OLLAMA = "ollama"
    AZURE = "azure"
    ANYSCALE = "anyscale"
    LOCALAI = "localai"


@dataclass
class ToolCall:
    """A single tool call returned by the LLM."""
    id: str
    name: str
    arguments: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "arguments": self.arguments}


@dataclass
class LLMResponse:
    """Standardized LLM response."""
    content: str
    confidence: float
    usage: dict
    model: str
    provider: str
    finish_reason: str
    tool_calls: list[ToolCall] = field(default_factory=list)


@dataclass
class LLMConfig:
    """LLM provider configuration"""
    provider: LLMProvider
    model: str
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    api_version: Optional[str] = None
    deployment_name: Optional[str] = None
    temperature: float = 0.7
    max_tokens: int = 2000
    timeout: int = 60


class LLMError(Exception):
    """Base exception for LLM errors"""
    pass


class LLMTimeoutError(LLMError):
    """Raised when LLM call times out"""
    pass


class LLMParseError(LLMError):
    """Raised when structured output parsing fails"""
    pass


class LLMProviderError(LLMError):
    """Raised when provider-specific error occurs"""
    pass


# Vietnamese-optimized models
VIETNAMESE_MODELS = {
    # Ollama models (good for Vietnamese)
    "llama3": {
        "provider": LLMProvider.OLLAMA,
        "model": "llama3",
        "description": "Meta's latest, excellent multilingual including Vietnamese",
        "context_length": 8192,
        "ram_required": "8GB"
    },
    "llama3.1": {
        "provider": LLMProvider.OLLAMA,
        "model": "llama3.1",
        "description": "Extended context, better multilingual",
        "context_length": 128000,
        "ram_required": "8GB"
    },
    "phi3": {
        "provider": LLMProvider.OLLAMA,
        "model": "phi3",
        "description": "Microsoft's efficient model, decent Vietnamese",
        "context_length": 4096,
        "ram_required": "4GB"
    },
    "phi3-medium": {
        "provider": LLMProvider.OLLAMA,
        "model": "phi3-medium",
        "description": "Better quality phi3, moderate Vietnamese",
        "context_length": 4096,
        "ram_required": "8GB"
    },
    "mistral": {
        "provider": LLMProvider.OLLAMA,
        "model": "mistral",
        "description": "Good multilingual, works for Vietnamese",
        "context_length": 8192,
        "ram_required": "6GB"
    },
    "mixtral": {
        "provider": LLMProvider.OLLAMA,
        "model": "mixtral",
        "description": "Mixture of experts, excellent quality",
        "context_length": 32768,
        "ram_required": "12GB"
    },
    "qwen2": {
        "provider": LLMProvider.OLLAMA,
        "model": "qwen2",
        "description": "Alibaba's model, good multilingual including Vietnamese",
        "context_length": 32768,
        "ram_required": "6GB"
    },
    # OpenAI models
    "gpt-4o": {
        "provider": LLMProvider.OPENAI,
        "model": "gpt-4o",
        "description": "OpenAI's flagship, excellent Vietnamese",
        "context_length": 128000,
        "ram_required": "N/A (cloud)"
    },
    "gpt-4-turbo": {
        "provider": LLMProvider.OPENAI,
        "model": "gpt-4-turbo",
        "description": "Fast GPT-4, good Vietnamese",
        "context_length": 128000,
        "ram_required": "N/A (cloud)"
    },
    "gpt-3.5-turbo": {
        "provider": LLMProvider.OPENAI,
        "model": "gpt-3.5-turbo",
        "description": "Fast and cheap, decent Vietnamese",
        "context_length": 16385,
        "ram_required": "N/A (cloud)"
    },
}


class MultiProviderLLMClient:
    """
    Multi-provider LLM client with:
    - Automatic provider detection
    - Ollama support for self-hosted
    - OpenAI / Azure OpenAI support
    - Vietnamese-optimized defaults
    - Circuit breaker pattern
    - Cost tracking
    """

    def __init__(self):
        self._clients: dict[LLMProvider, AsyncOpenAI] = {}
        self._active_model: str = "llama3"
        
        # Set explicit provider override from config
        explicit_provider = getattr(settings, 'llm_provider', '').lower().strip()
        if explicit_provider:
            self._explicit_provider = LLMProvider(explicit_provider)
            self._active_provider = self._explicit_provider
        else:
            self._explicit_provider = None
            self._active_provider = None
        self._temperature: float = settings.llm_temperature or 0.7
        self._max_tokens: int = settings.llm_max_tokens or 2000
        # Use ollama_timeout if set, otherwise agent_timeout
        self._timeout: int = getattr(settings, 'ollama_timeout', None) or settings.agent_timeout or 60

        # Circuit breaker
        self._circuit_breaker = get_circuit_breaker(
            "multi_llm_client",
            CircuitBreakerConfig(
                failure_threshold=5,
                success_threshold=2,
                timeout=30.0
            )
        )

        # Cost tracking
        self._total_cost: float = 0.0
        self._total_tokens: int = 0

        # Model pricing (USD per 1K tokens)
        self._pricing = {
            "gpt-4o": {"input": 0.005, "output": 0.015},
            "gpt-4-turbo": {"input": 0.01, "output": 0.03},
            "gpt-4": {"input": 0.03, "output": 0.06},
            "gpt-3.5-turbo": {"input": 0.0005, "output": 0.0015},
        }

        # Ollama base URL
        self._ollama_base_url = getattr(settings, 'ollama_base_url', 'http://localhost:11434')

    def get_provider(self, model: str = None) -> LLMProvider:
        """Get provider - explicit override or auto-detect"""
        if self._explicit_provider:
            return self._explicit_provider
        return self._detect_provider(model or self._active_model)

    def _detect_provider(self, model: str) -> LLMProvider:
        """Auto-detect provider from model name"""
        model_lower = model.lower()

        # Ollama models
        ollama_models = [
            "llama", "mistral", "mixtral", "phi", "qwen",
            "codellama", "vicuna", "orca", "wizard", "falcon",
            "stablelm", "neural", "tinydolphin", "dolphin",
            "aya", "command", "nemo", "solar", "gemma", " Gemma"
        ]
        if any(m in model_lower for m in ollama_models):
            # Check if it's not OpenAI's official model
            if not model_lower.startswith(("gpt-", "o1-", "o3-")):
                return LLMProvider.OLLAMA

        # Azure OpenAI
        if hasattr(settings, 'azure_openai_endpoint') and settings.azure_openai_endpoint:
            return LLMProvider.AZURE

        # Default to OpenAI
        return LLMProvider.OPENAI

    def _get_client(self, provider: LLMProvider) -> AsyncOpenAI:
        """Get or create client for provider"""
        if provider not in self._clients:
            if provider == LLMProvider.OLLAMA:
                self._clients[provider] = AsyncOpenAI(
                    base_url=f"{self._ollama_base_url}/v1",
                    api_key="ollama",  # Ollama doesn't need real key
                    timeout=self._timeout,
                    http_client=httpx.AsyncClient(timeout=httpx.Timeout(self._timeout)),
                )
            elif provider == LLMProvider.AZURE:
                self._clients[provider] = AsyncOpenAI(
                    api_key=settings.azure_openai_key,
                    azure_endpoint=settings.azure_openai_endpoint,
                    api_version=settings.azure_openai_api_version or "2024-02-01",
                    timeout=self._timeout,
                )
            else:  # OPENAI
                self._clients[provider] = AsyncOpenAI(
                    api_key=settings.openai_api_key,
                    timeout=self._timeout,
                )

        return self._clients[provider]

    async def initialize(self):
        """Initialize clients based on configuration"""
        # Determine active model
        configured_model = settings.llm_model or "llama3"
        self._active_model = configured_model
        self._active_provider = self.get_provider(configured_model)

        # Initialize client
        self._get_client(self._active_provider)

        logger.info(
            "Multi-provider LLM client initialized",
            model=self._active_model,
            provider=self._active_provider.value
        )

    async def close(self):
        """Close all clients"""
        for provider, client in self._clients.items():
            await client.close()
        logger.info("All LLM clients closed", total_cost=self._total_cost)

    @property
    def is_initialized(self) -> bool:
        return len(self._clients) > 0

    @property
    def active_model(self) -> str:
        return self._active_model

    @property
    def active_provider(self) -> str:
        return self._active_provider.value if self._active_provider else "unknown"

    def set_model(self, model: str):
        """Switch to a different model"""
        new_provider = self.get_provider(model)

        # Get new client if needed
        if new_provider not in self._clients:
            self._get_client(new_provider)

        self._active_model = model
        self._active_provider = new_provider

        logger.info("Model switched", model=model, provider=new_provider.value)

    def set_base_url(self, provider: str, url: str):
        """Set custom base URL for a provider.
        
        Args:
            provider: Provider name ("ollama", "openai", "azure")
            url: Base URL (e.g., "http://localhost:8088" for llama.cpp)
        """
        provider_upper = provider.upper()
        try:
            prov = LLMProvider[provider_upper]
        except KeyError:
            logger.warning("Unknown provider for set_base_url", provider=provider)
            return

        # Update base URL based on provider
        if prov == LLMProvider.OLLAMA:
            self._ollama_base_url = url.rstrip('/')
            # Force client recreation on next call
            if prov in self._clients:
                del self._clients[prov]
            logger.info("Ollama base URL updated", url=self._ollama_base_url)
        else:
            # For OpenAI/Azure, we need to recreate client with new base_url
            if prov in self._clients:
                del self._clients[prov]
            # Get API key from settings or use placeholder
            api_key = "not-provided"
            if prov == LLMProvider.OPENAI:
                api_key = getattr(self._settings, 'openai_api_key', None) or "not-provided"
            elif prov == LLMProvider.AZURE:
                api_key = getattr(self._settings, 'azure_openai_key', None) or "not-provided"
            
            self._clients[prov] = AsyncOpenAI(
                base_url=f"{url.rstrip('/')}/v1",
                api_key=api_key,
                timeout=self._timeout,
            )
            logger.info("Custom base URL set", provider=provider, url=url)

    def reset_circuit_breaker(self, provider: str = None):
        """Reset circuit breaker state.
        
        Args:
            provider: Optional provider name. If None, resets all.
        """
        if provider:
            # Reset specific provider - this would require storing per-provider breakers
            # For now, just log and reset the main one
            logger.info("Circuit breaker reset requested", provider=provider)
        
        # Reset the main circuit breaker
        self._circuit_breaker._state = CircuitState.CLOSED
        self._circuit_breaker._failure_count = 0
        self._circuit_breaker.metrics = CircuitBreakerMetrics()
        logger.info("Circuit breaker reset", name="multi_llm_client")

    def get_available_models(self) -> dict:
        """Get all available models info"""
        return VIETNAMESE_MODELS.copy()

    async def health_check(self) -> dict:
        """Check health of all providers"""
        results = {}

        # Check Ollama
        try:
            if LLMProvider.OLLAMA in self._clients:
                ollama_client = self._clients[LLMProvider.OLLAMA]
                await ollama_client.chat.completions.create(
                    model="llama3",
                    messages=[{"role": "user", "content": "ping"}],
                    max_tokens=5
                )
                results["ollama"] = {
                    "status": "healthy",
                    "model": "llama3",
                    "response_time": "fast"
                }
            else:
                # Try to connect
                async with httpx.AsyncClient() as client:
                    resp = await client.get(f"{self._ollama_base_url}/api/tags", timeout=5)
                    if resp.status_code == 200:
                        results["ollama"] = {"status": "available", "models": resp.json().get("models", [])}
                    else:
                        results["ollama"] = {"status": "unavailable"}
        except Exception as e:
            results["ollama"] = {"status": "error", "error": str(e)}

        # Check OpenAI
        if settings.openai_api_key:
            try:
                openai_client = self._get_client(LLMProvider.OPENAI)
                await openai_client.chat.completions.create(
                    model="gpt-3.5-turbo",
                    messages=[{"role": "user", "content": "ping"}],
                    max_tokens=5
                )
                results["openai"] = {"status": "healthy"}
            except Exception as e:
                results["openai"] = {"status": "error", "error": str(e)}

        return results

    def _calculate_cost(self, model: str, usage: dict) -> float:
        """Calculate cost based on token usage"""
        # Only cloud models have cost
        if self.get_provider(model) == LLMProvider.OLLAMA:
            return 0.0

        # Safely extract usage dict (handle CompletionUsage object)
        if hasattr(usage, '__dict__'):
            usage = usage.__dict__
        elif not isinstance(usage, dict):
            usage = {}

        pricing = self._pricing.get(model, {"input": 0.01, "output": 0.03})
        input_cost = (usage.get("prompt_tokens", 0) / 1000) * pricing["input"]
        output_cost = (usage.get("completion_tokens", 0) / 1000) * pricing["output"]
        return input_cost + output_cost

    def _extract_usage(self, usage) -> dict:
        """Safely convert usage object to dict"""
        if usage is None:
            return {}
        if hasattr(usage, '__dict__'):
            return usage.__dict__
        if isinstance(usage, dict):
            return usage
        return {}

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception_type((TimeoutError, ConnectionError, httpx.TimeoutException)),
        reraise=True,
    )
    async def complete(
        self,
        system_prompt: str,
        user_message: str,
        context: Optional[dict] = None,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        tools: Optional[List[Dict]] = None,
    ) -> LLMResponse:
        """
        Generate completion with automatic provider detection.

        If `tools` is provided, the LLM is instructed to use function-calling.
        Tool calls (if any) are returned in LLMResponse.tool_calls.
        """
        target_model = model or self._active_model
        target_provider = self.get_provider(target_model)
        client = self._get_client(target_provider)

        messages: List[Dict] = [{"role": "system", "content": system_prompt}]
        if context:
            context_str = self._format_context(context)
            messages.append({"role": "system", "content": f"Context:\n{context_str}"})

        messages.append({"role": "user", "content": user_message})

        if not await self._circuit_breaker.can_execute():
            raise CircuitBreakerError("multi_llm", "Circuit breaker is open")

        try:
            kwargs: Dict[str, Any] = {"messages": messages}
            if target_provider == LLMProvider.AZURE:
                kwargs["deployment_id"] = settings.azure_deployment_name or target_model
            else:
                kwargs["model"] = target_model

            if tools:
                kwargs["tools"] = tools
                kwargs["tool_choice"] = "auto"

            create_kwargs = {
                **kwargs,
                "temperature": temperature or self._temperature,
                "max_tokens": max_tokens or self._max_tokens,
            }

            response = await client.chat.completions.create(**create_kwargs)

            await self._circuit_breaker.record_success()

            message = response.choices[0].message
            content = message.content or ""
            usage = response.usage
            usage_dict = self._extract_usage(usage)
            finish_reason = str(response.choices[0].finish_reason)

            # Parse tool calls
            tool_calls: List[ToolCall] = []
            if hasattr(message, "tool_calls") and message.tool_calls:
                for tc in message.tool_calls:
                    try:
                        args_str = tc.function.arguments
                        args = json.loads(args_str) if isinstance(args_str, str) else args_str
                    except (json.JSONDecodeError, TypeError):
                        args = {"raw": str(tc.function.arguments)}
                    tool_calls.append(
                        ToolCall(
                            id=str(tc.index or "") + "_" + str(hash(args_str if isinstance(args_str, str) else "")),
                            name=tc.function.name,
                            arguments=args,
                        )
                    )

            cost = self._calculate_cost(target_model, usage)
            self._total_cost += cost
            self._total_tokens += usage_dict.get("total_tokens", 0)

            logger.debug(
                "LLM completion",
                provider=target_provider.value,
                model=target_model,
                tokens=usage_dict.get("total_tokens", 0),
                cost_usd=round(cost, 6),
                tool_calls=len(tool_calls),
            )

            return LLMResponse(
                content=content,
                confidence=self._calculate_confidence(finish_reason),
                usage=usage_dict,
                model=target_model,
                provider=target_provider.value,
                finish_reason=finish_reason,
                tool_calls=tool_calls,
            )

        except Exception as e:
            await self._circuit_breaker.record_failure()
            logger.error(
                "LLM completion failed",
                provider=target_provider.value,
                model=target_model,
                error=str(e),
            )
            raise LLMError(f"Completion failed: {e}")

    async def complete_structured(
        self,
        system_prompt: str,
        user_message: str,
        output_schema: Type[T],
        context: Optional[dict] = None,
        model: Optional[str] = None,
        temperature: float = 0.3,
    ) -> T:
        """
        Generate structured JSON output matching a schema
        """
        target_model = model or self._active_model
        # Build schema description
        schema_fields = []
        for field_name, field_info in output_schema.__annotations__.items():
            field_type = field_info if isinstance(field_info, str) else str(field_info)
            schema_fields.append(f"- {field_name}: {field_type}")

        schema_description = "\n".join(schema_fields)

        full_system = f"""{system_prompt}

Output Schema (respond ONLY with valid JSON):
{schema_description}

IMPORTANT:
- Respond with ONLY valid JSON matching the schema
- No markdown, no explanation, just the JSON object"""

        response = await self.complete(
            system_prompt=full_system,
            user_message=user_message,
            context=context,
            model=target_model,
            temperature=temperature,
        )

        try:
            data = json.loads(response.content)
            return output_schema(**data)
        except (json.JSONDecodeError, TypeError) as e:
            logger.error("Failed to parse structured output", error=str(e), content=response.content)
            raise LLMParseError(f"Failed to parse structured output: {e}")

    async def chat(
        self,
        messages: list[dict],
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> LLMResponse:
        """
        Direct chat completion
        """
        target_model = model or self._active_model
        target_provider = self.get_provider(target_model)
        client = self._get_client(target_provider)

        if not await self._circuit_breaker.can_execute():
            raise CircuitBreakerError("multi_llm", "Circuit breaker is open")

        try:
            if target_provider == LLMProvider.AZURE:
                kwargs = {
                    "messages": messages,
                    "deployment_id": settings.azure_deployment_name or target_model,
                }
            else:
                kwargs = {
                    "model": target_model,
                    "messages": messages,
                }

            response = await client.chat.completions.create(
                **kwargs,
                temperature=temperature or self._temperature,
                max_tokens=max_tokens or self._max_tokens,
            )

            await self._circuit_breaker.record_success()

            content = response.choices[0].message.content or ""
            usage = response.usage
            
            # Convert usage to dict (Ollama returns CompletionUsage object, not dict)
            if hasattr(usage, '__dict__'):
                usage = usage.__dict__
            elif not isinstance(usage, dict):
                usage = {}
                
            finish_reason = response.choices[0].finish_reason

            cost = self._calculate_cost(target_model, usage)
            self._total_cost += cost
            usage_dict = self._extract_usage(usage)
            self._total_tokens += usage_dict.get("total_tokens", 0)

            return LLMResponse(
                content=content,
                confidence=self._calculate_confidence(finish_reason),
                usage=usage_dict,
                model=target_model,
                provider=target_provider.value,
                finish_reason=finish_reason
            )

        except Exception as e:
            await self._circuit_breaker.record_failure()
            raise LLMError(f"Chat failed: {e}")

    async def classify_intent(
        self,
        message: str,
        context: str = "",
        available_intents: Optional[list[str]] = None,
        model: Optional[str] = None,
    ) -> dict:
        """
        Classify intent using LLM
        Vietnamese-optimized prompts
        """
        intents = available_intents or [
            "faq", "policy", "support_case", "analysis",
            "executive_request", "general"
        ]

        system = f"""Bạn là một intent classifier cho hệ thống supervisor.
Classify tin nhắn của người dùng vào MỘT trong các intents sau:
{', '.join(intents)}

Trả về JSON với format:
{{"intent": "...", "confidence": 0.0-1.0, "reasoning": "..."}}

CHỉ trả về JSON, không giải thích gì thêm."""

        response = await self.complete(
            system_prompt=system,
            user_message=f"Tin nhắn: {message}\nNgữ cảnh: {context}",
            model=model,
            temperature=0.3,
        )

        try:
            parsed = json.loads(response.content)
            return parsed
        except json.JSONDecodeError:
            return {"intent": "general", "confidence": 0.4, "reasoning": "Parse failed"}

    def _format_context(self, context: dict) -> str:
        """Format context dictionary for prompt"""
        parts = []
        for key, value in context.items():
            if isinstance(value, (list, dict)):
                parts.append(f"{key}: {json.dumps(value)[:500]}")
            elif value:
                parts.append(f"{key}: {str(value)[:500]}")
        return "\n".join(parts)

    def _calculate_confidence(self, finish_reason: str) -> float:
        """Calculate confidence from finish reason.

        Keep the provider-side default conservative; the supervisor may still
        promote a response later when KB evidence and QA validation both support it.
        """
        if finish_reason == "stop":
            return 0.45
        elif finish_reason == "length":
            return 0.35
        elif finish_reason == "content_filter":
            return 0.25
        return 0.4

    def get_cost_stats(self) -> dict:
        """Get cost tracking statistics"""
        return {
            "total_cost_usd": round(self._total_cost, 6),
            "total_tokens": self._total_tokens,
            "active_model": self._active_model,
            "active_provider": self.active_provider,
            "pricing": self._pricing.get(self._active_model, {}),
        }


# Singleton instance
llm_client = MultiProviderLLMClient()


async def get_llm_client() -> MultiProviderLLMClient:
    """Get the multi-provider LLM client singleton"""
    return llm_client
