"""
Test script for Ollama Multi-Provider LLM Client
"""
import asyncio
import os
import sys

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.llm.provider import (
    MultiProviderLLMClient,
    LLMProvider,
    VIETNAMESE_MODELS,
    get_llm_client,
)


async def test_ollama_connection():
    """Test Ollama connection"""
    print("=" * 60)
    print("Testing Ollama Connection")
    print("=" * 60)

    client = MultiProviderLLMClient()

    # Health check
    print("\n1. Health Check...")
    health = await client.health_check()
    print(f"   Health status: {health}")

    if "ollama" in health and health["ollama"].get("status") == "available":
        print("   ✓ Ollama is available!")

        # List models
        models = health["ollama"].get("models", [])
        print(f"\n   Installed models: {len(models)}")
        for m in models[:5]:
            print(f"   - {m.get('name', 'unknown')}")

        return True
    else:
        print("   ✗ Ollama is not available")
        print("   Install with: curl -fsSL https://ollama.com/install.sh | sh")
        print("   Then run: ollama pull llama3")
        return False


async def _test_vietnamese_completion(client: MultiProviderLLMClient):
    """Test Vietnamese language completion"""
    print("\n" + "=" * 60)
    print("Testing Vietnamese Language Processing")
    print("=" * 60)

    test_prompts = [
        {
            "system": "Bạn là một trợ lý AI hữu ích. Trả lời ngắn gọn.",
            "user": "Xin chào, bạn tên gì?",
            "description": "Basic Vietnamese greeting"
        },
        {
            "system": "Bạn là một chuyên gia IT. Trả lời ngắn gọn và chính xác.",
            "user": "Giải thích về VPN trong 2 câu.",
            "description": "IT terminology in Vietnamese"
        },
        {
            "system": "Bạn là một chuyên gia phân tích dữ liệu. Trả lời ngắn gọn.",
            "user": "Cho biết 3 lợi ích của Business Intelligence.",
            "description": "Business Vietnamese"
        }
    ]

    for i, test in enumerate(test_prompts, 1):
        print(f"\n{i}. {test['description']}")
        print(f"   User: {test['user']}")

        try:
            response = await client.complete(
                system_prompt=test["system"],
                user_message=test["user"],
                max_tokens=200,
            )

            print(f"   Model: {response.model} ({response.provider})")
            print(f"   Response: {response.content[:200]}...")
            print(f"   Confidence: {response.confidence:.2f}")
            print(f"   ✓ Success!")

        except Exception as e:
            print(f"   ✗ Error: {e}")


async def _test_intent_classification(client: MultiProviderLLMClient):
    """Test Vietnamese intent classification"""
    print("\n" + "=" * 60)
    print("Testing Intent Classification (Vietnamese)")
    print("=" * 60)

    test_intents = [
        "Làm sao để reset password?",
        "Báo cáo doanh thu tháng này",
        "Tôi cần hỗ trợ về VPN",
        "Xin chào, có ai không?",
    ]

    for i, message in enumerate(test_intents, 1):
        print(f"\n{i}. Message: {message}")

        try:
            result = await client.classify_intent(
                message=message,
                context="IT Support System"
            )

            print(f"   Intent: {result.get('intent')}")
            print(f"   Confidence: {result.get('confidence', 0):.2f}")
            print(f"   Reasoning: {result.get('reasoning', 'N/A')}")

        except Exception as e:
            print(f"   ✗ Error: {e}")


async def _test_model_switching(client: MultiProviderLLMClient):
    """Test switching between models"""
    print("\n" + "=" * 60)
    print("Testing Model Switching")
    print("=" * 60)

    models = ["llama3", "phi3", "mistral"]

    for model in models:
        print(f"\n- Switching to {model}...")
        try:
            client.set_model(model)
            response = await client.complete(
                system_prompt="Trả lời ngắn: Bạn là AI nào?",
                user_message="Ping",
                max_tokens=50,
            )
            print(f"  ✓ {model}: {response.content[:50]}...")

        except Exception as e:
            print(f"  ✗ {model}: {e}")


async def main():
    """Run all tests"""
    print("\n" + "=" * 60)
    print("MULTI-PROVIDER LLM CLIENT TEST")
    print("=" * 60)

    # Show available models
    print("\nAvailable Models:")
    print("-" * 40)
    for name, info in VIETNAMESE_MODELS.items():
        print(f"  {name:15} | {info['provider'].value:8} | {info['description'][:40]}")

    # Initialize client
    client = MultiProviderLLMClient()
    await client.initialize()

    print(f"\nActive Model: {client.active_model}")
    print(f"Active Provider: {client.active_provider}")

    # Test Ollama connection
    ollama_available = await test_ollama_connection()

    if not ollama_available:
        print("\n⚠️  Ollama not available. Some tests will be skipped.")
        print("   To install: curl -fsSL https://ollama.com/install.sh | sh")

        # Still test OpenAI if available
        if os.getenv("OPENAI_API_KEY"):
            print("\n   Testing OpenAI as fallback...")
            client.set_model("gpt-3.5-turbo")

    # Run tests
    if ollama_available:
        await _test_vietnamese_completion(client)
        await _test_intent_classification(client)
        await _test_model_switching(client)

    # Show cost stats
    print("\n" + "=" * 60)
    print("Cost Statistics")
    print("=" * 60)
    stats = client.get_cost_stats()
    print(f"  Total Cost: ${stats['total_cost_usd']:.6f}")
    print(f"  Total Tokens: {stats['total_tokens']}")
    print(f"  Active Model: {stats['active_model']}")
    print(f"  Provider: {stats['active_provider']}")

    # Cleanup
    await client.close()
    print("\n✓ All tests completed!")


if __name__ == "__main__":
    asyncio.run(main())
