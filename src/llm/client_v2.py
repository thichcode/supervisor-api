"""
Enhanced LLM Client with Structured Output and Circuit Breaker
Replaces the basic client.py for production use
"""
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletion
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)
from typing import Optional, Type, TypeVar, Any
from dataclasses import dataclass
import structlog
import json

from src.config import get_settings
from src.core.circuit_breaker import get_circuit_breaker, CircuitBreakerConfig, CircuitBreakerError

settings = get_settings()
logger = structlog.get_logger()

T = TypeVar('T')


@dataclass
class LLMResponse:
    content: str
    confidence: float
    usage: dict
    model: str
    finish_reason: str


class LLMError(Exception):
    """Base exception for LLM errors"""
    pass


class LLMTimeoutError(LLMError):
    """Raised when LLM call times out"""
    pass


class LLMParseError(LLMError):
    """Raised when structured output parsing fails"""
    pass


class EnhancedLLMClient:
    """
    Enhanced LLM client with:
    - Circuit breaker
    - Structured JSON output
    - Better error handling
    - Cost tracking
    """
    
    def __init__(
        self,
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 2000,
        timeout: int = 30
    ):
        self._client: Optional[AsyncOpenAI] = None
        self._model = model or settings.llm_model or "gpt-4o"
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._timeout = timeout
        
        # Circuit breaker for this LLM
        self._circuit_breaker = get_circuit_breaker(
            "llm_client",
            CircuitBreakerConfig(
                failure_threshold=5,
                success_threshold=2,
                timeout=30.0
            )
        )
        
        # Cost tracking
        self._total_cost = 0.0
        self._total_tokens = 0
        
        # Model pricing (approximate, USD per 1K tokens)
        self._pricing = {
            "gpt-4o": {"input": 0.005, "output": 0.015},
            "gpt-4-turbo": {"input": 0.01, "output": 0.03},
            "gpt-4": {"input": 0.03, "output": 0.06},
            "gpt-3.5-turbo": {"input": 0.0005, "output": 0.0015},
        }

    async def initialize(self):
        """Initialize the OpenAI client"""
        if not settings.openai_api_key:
            logger.warning("LLM client initialized without API key")
            return
            
        self._client = AsyncOpenAI(
            api_key=settings.openai_api_key,
            timeout=self._timeout,
            max_retries=0,  # We handle retries ourselves
        )
        logger.info("Enhanced LLM client initialized", model=self._model)

    async def close(self):
        """Close the client"""
        if self._client:
            await self._client.close()
            logger.info(
                "Enhanced LLM client closed",
                total_cost=self._total_cost,
                total_tokens=self._total_tokens
            )

    @property
    def is_initialized(self) -> bool:
        return self._client is not None

    async def health_check(self) -> bool:
        """Check if LLM is healthy"""
        if not self._client:
            return False
        try:
            await self.complete(
                system_prompt="Reply with OK.",
                user_message="ping",
                max_tokens=10
            )
            return True
        except Exception as e:
            logger.warning("LLM health check failed", error=str(e))
            return False

    def _calculate_cost(self, usage: dict) -> float:
        """Calculate cost based on token usage"""
        pricing = self._pricing.get(self._model, {"input": 0.01, "output": 0.03})
        input_cost = (usage.get("prompt_tokens", 0) / 1000) * pricing["input"]
        output_cost = (usage.get("completion_tokens", 0) / 1000) * pricing["output"]
        return input_cost + output_cost

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception_type((TimeoutError, ConnectionError)),
        reraise=True,
    )
    async def complete(
        self,
        system_prompt: str,
        user_message: str,
        context: Optional[dict] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> tuple[str, float]:
        """Generate a completion with automatic retries"""
        if not self._client:
            raise LLMError("LLM client not initialized")

        # Check circuit breaker
        if not await self._circuit_breaker.can_execute():
            raise CircuitBreakerError(
                "llm_client",
                "LLM circuit breaker is open"
            )

        messages = [{"role": "system", "content": system_prompt}]
        
        if context:
            context_str = self._format_context(context)
            messages.append({"role": "system", "content": f"Context:\n{context_str}"})

        messages.append({"role": "user", "content": user_message})

        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                temperature=temperature or self._temperature,
                max_tokens=max_tokens or self._max_tokens,
            )

            # Record success
            await self._circuit_breaker.record_success()

            content = response.choices[0].message.content or ""
            usage = response.usage or {}
            
            # Track cost
            cost = self._calculate_cost(usage)
            self._total_cost += cost
            self._total_tokens += usage.get("total_tokens", 0)

            logger.debug(
                "LLM completion",
                model=self._model,
                tokens=usage.get("total_tokens", 0),
                cost_usd=round(cost, 6)
            )

            confidence = self._calculate_confidence(response)
            return content, confidence

        except Exception as e:
            await self._circuit_breaker.record_failure()
            logger.error("LLM completion failed", error=str(e))
            raise

    async def complete_structured(
        self,
        system_prompt: str,
        user_message: str,
        output_schema: Type[T],
        context: Optional[dict] = None,
        temperature: float = 0.3,
    ) -> T:
        """
        Generate structured JSON output that matches a schema.
        Uses gpt-4o JSON mode for reliable parsing.
        """
        if not self._client:
            raise LLMError("LLM client not initialized")

        if not await self._circuit_breaker.can_execute():
            raise CircuitBreakerError("llm_client", "LLM circuit breaker is open")

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

        messages = [
            {"role": "system", "content": full_system},
        ]
        
        if context:
            context_str = self._format_context(context)
            messages.append({"role": "system", "content": f"Context:\n{context_str}"})

        messages.append({"role": "user", "content": user_message})

        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                temperature=temperature,
                max_tokens=self._max_tokens,
                response_format={"type": "json_object"},
            )

            await self._circuit_breaker.record_success()

            content = response.choices[0].message.content or ""
            
            # Parse JSON
            try:
                data = json.loads(content)
                result = output_schema(**data)
                return result
            except (json.JSONDecodeError, TypeError) as e:
                logger.error("Failed to parse structured output", error=str(e), content=content)
                raise LLMParseError(f"Failed to parse structured output: {e}")

        except Exception as e:
            await self._circuit_breaker.record_failure()
            if not isinstance(e, (CircuitBreakerError, LLMParseError)):
                raise LLMError(f"Structured completion failed: {e}")
            raise

    async def classify_intent(
        self,
        message: str,
        context: str = "",
        available_intents: Optional[list[str]] = None
    ) -> dict:
        """Classify intent with structured output"""
        intents = available_intents or [
            "faq", "policy", "support_case", "analysis", "executive_request"
        ]
        
        system = f"""You are an intent classifier.
Classify into ONE of: {', '.join(intents)}
Return JSON: {{"intent": "...", "confidence": 0.0-1.0, "reasoning": "..."}}"""
        
        result, confidence = await self.complete(
            system_prompt=system,
            user_message=f"Message: {message}\nContext: {context}",
            temperature=0.3
        )
        
        try:
            parsed = json.loads(result)
            return parsed
        except json.JSONDecodeError:
            return {"intent": "faq", "confidence": 0.5, "reasoning": "Parse failed"}

    def get_cost_stats(self) -> dict:
        """Get cost tracking statistics"""
        return {
            "total_cost_usd": round(self._total_cost, 6),
            "total_tokens": self._total_tokens,
            "model": self._model,
            "pricing": self._pricing.get(self._model, {})
        }

    def _format_context(self, context: dict) -> str:
        """Format context dictionary for prompt"""
        parts = []
        for key, value in context.items():
            if isinstance(value, (list, dict)):
                parts.append(f"{key}: {json.dumps(value)[:500]}")
            elif value:
                parts.append(f"{key}: {str(value)[:500]}")
        return "\n".join(parts)

    def _calculate_confidence(self, response: ChatCompletion) -> float:
        """Calculate confidence score from response"""
        if not response.choices:
            return 0.5
            
        choice = response.choices[0]
        finish_reason = choice.finish_reason
        
        if finish_reason == "stop":
            return 0.9
        elif finish_reason == "length":
            return 0.7
        elif finish_reason == "content_filter":
            return 0.5
        return 0.8


# Singleton instance
llm_client = EnhancedLLMClient()


async def get_llm_client() -> EnhancedLLMClient:
    """Get the enhanced LLM client singleton"""
    return llm_client
