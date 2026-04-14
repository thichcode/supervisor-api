"""
Tests for Multi-Provider LLM Client
"""
import pytest
from src.llm import MultiProviderLLMClient, LLMResponse, LLMProvider


class TestMultiProviderLLMClient:
    """Test suite for MultiProviderLLMClient"""

    @pytest.fixture
    def client(self):
        """Create a fresh client for each test"""
        return MultiProviderLLMClient()

    def test_client_initialization(self, client):
        """Test client initializes with correct defaults"""
        assert client._active_model == "llama3"
        assert client._temperature == 0.7
        assert client._max_tokens == 2000
        # Timeout defaults to settings.agent_timeout (10) if not specified
        assert client._timeout > 0

    def test_detect_provider_ollama(self, client):
        """Test Ollama model detection"""
        assert client._detect_provider("llama3") == LLMProvider.OLLAMA
        assert client._detect_provider("llama3.1") == LLMProvider.OLLAMA
        assert client._detect_provider("phi3") == LLMProvider.OLLAMA
        assert client._detect_provider("mistral") == LLMProvider.OLLAMA
        assert client._detect_provider("mixtral") == LLMProvider.OLLAMA
        assert client._detect_provider("qwen2") == LLMProvider.OLLAMA
        assert client._detect_provider("codellama") == LLMProvider.OLLAMA

    def test_detect_provider_openai(self, client):
        """Test OpenAI model detection"""
        assert client._detect_provider("gpt-4") == LLMProvider.OPENAI
        assert client._detect_provider("gpt-4o") == LLMProvider.OPENAI
        assert client._detect_provider("gpt-3.5-turbo") == LLMProvider.OPENAI

    def test_set_model(self, client):
        """Test model switching"""
        client.set_model("phi3")
        assert client.active_model == "phi3"
        assert client.active_provider == "ollama"

        client.set_model("gpt-4o")
        assert client.active_model == "gpt-4o"
        assert client.active_provider == "openai"

    def test_get_available_models(self, client):
        """Test getting available models"""
        models = client.get_available_models()
        assert "llama3" in models
        assert "phi3" in models
        assert "mistral" in models
        assert "gpt-4o" in models
        assert models["llama3"]["provider"].value == "ollama"

    def test_get_cost_stats(self, client):
        """Test cost statistics"""
        stats = client.get_cost_stats()
        assert "total_cost_usd" in stats
        assert "total_tokens" in stats
        assert "active_model" in stats
        assert "active_provider" in stats


class TestLLMResponse:
    """Test LLMResponse dataclass"""

    def test_response_creation(self):
        """Test creating LLMResponse"""
        response = LLMResponse(
            content="Xin chào",
            confidence=0.9,
            usage={"prompt_tokens": 10, "completion_tokens": 5},
            model="llama3",
            provider="ollama",
            finish_reason="stop"
        )
        assert response.content == "Xin chào"
        assert response.confidence == 0.9
        assert response.model == "llama3"
        assert response.provider == "ollama"


class TestVietnamesePrompts:
    """Test Vietnamese-specific functionality"""

    @pytest.mark.asyncio
    async def test_vietnamese_intent_classification_prompt(self):
        """Test Vietnamese intent classification uses Vietnamese prompts"""
        client = MultiProviderLLMClient()

        # Mock the complete method to capture the prompt
        captured_prompts = {}

        async def mock_complete(system_prompt, user_message, **kwargs):
            captured_prompts["system"] = system_prompt
            captured_prompts["user"] = user_message
            return LLMResponse(
                content='{"intent": "faq", "confidence": 0.9, "reasoning": "test"}',
                confidence=0.9,
                usage={},
                model="llama3",
                provider="ollama",
                finish_reason="stop"
            )

        client.complete = mock_complete

        await client.classify_intent(
            message="Làm sao để reset password?",
            context="IT Support"
        )

        # Verify Vietnamese is used in prompts
        assert "Vietnamese" in captured_prompts["system"] or "Bạn là" in captured_prompts["system"]


class TestBackwardCompatibility:
    """Test backward compatibility with legacy LLMClient"""

    def test_llm_client_is_multiprovider(self):
        """Test that llm_client from src.llm is MultiProviderLLMClient"""
        from src.llm import llm_client
        assert isinstance(llm_client, MultiProviderLLMClient)

    def test_legacy_import_still_works(self):
        """Test that legacy LLMClient import still works"""
        from src.llm import LLMClient
        assert LLMClient is MultiProviderLLMClient

    @pytest.mark.asyncio
    async def test_get_llm_client_works(self):
        """Test get_llm_client function"""
        from src.llm import get_llm_client
        client = await get_llm_client()
        assert isinstance(client, MultiProviderLLMClient)
