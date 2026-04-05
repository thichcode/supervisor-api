from openai import AsyncOpenAI
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)
from typing import Optional
import structlog

from src.config import get_settings

settings = get_settings()
logger = structlog.get_logger()


class LLMError(Exception):
    pass


class LLMClient:
    def __init__(self):
        self._client: Optional[AsyncOpenAI] = None
        self._model = settings.llm_model or "gpt-4"
        self._temperature = settings.llm_temperature or 0.7
        self._max_tokens = settings.llm_max_tokens or 2000

    async def initialize(self):
        self._client = AsyncOpenAI(
            api_key=settings.openai_api_key,
            timeout=settings.agent_timeout,
            max_retries=0,
        )
        logger.info("LLM client initialized", model=self._model)

    async def close(self):
        if self._client:
            await self._client.close()
            logger.info("LLM client closed")

    @property
    def is_initialized(self) -> bool:
        return self._client is not None

    async def health_check(self) -> bool:
        if not settings.openai_api_key:
            return False
        if not self.is_initialized:
            return False
        if not settings.llm_healthcheck_enabled:
            return True

        try:
            await self.complete(
                system_prompt="Reply with OK only.",
                user_message="healthcheck",
            )
            return True
        except Exception as e:
            logger.warning("LLM health check failed", error=str(e))
            return False

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((TimeoutError, ConnectionError)),
        reraise=True,
    )
    async def complete(
        self,
        system_prompt: str,
        user_message: str,
        context: Optional[dict] = None,
    ) -> tuple[str, float]:
        if not self._client:
            raise LLMError("LLM client not initialized")

        messages = [{"role": "system", "content": system_prompt}]

        if context:
            context_str = self._format_context(context)
            messages.append({"role": "system", "content": f"Context:\n{context_str}"})

        messages.append({"role": "user", "content": user_message})

        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                temperature=self._temperature,
                max_tokens=self._max_tokens,
            )

            content = response.choices[0].message.content or ""
            usage = response.usage
            prompt_tokens = usage.prompt_tokens if usage else 0
            completion_tokens = usage.completion_tokens if usage else 0

            logger.debug(
                "LLM completion",
                model=self._model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            )

            return content, self._calculate_confidence(response)

        except Exception as e:
            logger.error("LLM completion failed", error=str(e))
            raise LLMError(f"LLM completion failed: {e}")

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    async def classify_intent_llm(
        self,
        message: str,
        context: str,
    ) -> dict:
        system_prompt = """You are an intent classifier for a supervisor system.
Classify the user's message into one of these intents:
- faq: Frequently asked questions
- policy: Policy or guideline related questions
- support_case: Support ticket or case related
- analysis: Data analysis or report requests
- executive_request: Requests from executives or VIP users

Return ONLY valid JSON:
{"intent": "<intent>", "confidence": <0.0-1.0>, "reasoning": "<brief explanation>"}"""

        result, _ = await self.complete(system_prompt, f"Message: {message}\n\nContext: {context}")
        return self._parse_json_response(result)

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    async def generate_response(
        self,
        message: str,
        context: dict,
        policy: dict,
        knowledge: dict,
    ) -> tuple[str, float]:
        system_prompt = """You are a helpful AI assistant for a corporate supervisor system.
Generate a helpful, accurate, and professional response based on the provided context.

Guidelines:
- Be concise but thorough
- Use appropriate tone based on user role
- Reference relevant policies and previous conversations
- Flag any commitments or important decisions
- If unsure, say you need to verify"""

        user_prompt = f"""User message: {message}

Conversation context: {context.get('summary', 'No prior context')}
Recent messages: {context.get('recent_messages', [])}
User role: {context.get('user_role', 'employee')}

Policy info: {policy}
Knowledge: {knowledge}"""

        return await self.complete(system_prompt, user_prompt)

    def _format_context(self, context: dict) -> str:
        parts = []
        for key, value in context.items():
            if isinstance(value, (list, dict)):
                parts.append(f"{key}: {value}")
            elif value:
                parts.append(f"{key}: {value}")
        return "\n".join(parts)

    def _parse_json_response(self, response: str) -> dict:
        import json
        import re

        json_match = re.search(r"\{[^{}]*\}", response, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group())
            except json.JSONDecodeError:
                pass

        try:
            return json.loads(response)
        except json.JSONDecodeError:
            return {"intent": "faq", "confidence": 0.5, "reasoning": "Parse failed, defaulting"}

    def _calculate_confidence(self, response) -> float:
        if hasattr(response, "choices") and response.choices:
            choice = response.choices[0]
            if hasattr(choice, "finish_reason"):
                if choice.finish_reason == "stop":
                    return 0.9
                elif choice.finish_reason == "length":
                    return 0.7
        return 0.8


llm_client = LLMClient()


async def get_llm() -> LLMClient:
    return llm_client
