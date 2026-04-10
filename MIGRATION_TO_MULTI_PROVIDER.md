# Migration Guide: Multi-Provider LLM Client

## Overview

This guide helps you migrate from the single-provider LLM client to the new multi-provider client that supports Ollama, OpenAI, and Azure OpenAI.

## Changes

### 1. Updated Config (`src/config.py`)

New environment variables:

```bash
# Ollama Configuration
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_DEFAULT_MODEL=llama3
OLLAMA_TIMEOUT=120

# Azure OpenAI (optional)
AZURE_OPENAI_ENDPOINT=
AZURE_OPENAI_KEY=
AZURE_OPENAI_API_VERSION=2024-02-01
AZURE_DEPLOYMENT_NAME=

# Default model changed from "gpt-4" to "llama3"
LLM_MODEL=llama3
```

### 2. New Import Pattern

**Before:**
```python
from src.llm import LLMClient, llm_client, get_llm
```

**After:**
```python
from src.llm import MultiProviderLLMClient, get_llm_client
```

### 3. Usage Changes

**Before:**
```python
client = LLMClient()
await client.initialize()

# Simple completion
content, confidence = await client.complete(
    system_prompt="You are helpful.",
    user_message="Hello"
)
```

**After:**
```python
client = MultiProviderLLMClient()
await client.initialize()

# Completion returns LLMResponse object
response = await client.complete(
    system_prompt="Bạn là trợ lý hữu ích.",
    user_message="Xin chào"
)

# Access response properties
print(response.content)
print(response.confidence)
print(response.model)
print(response.provider)
```

### 4. Intent Classification

**Before:**
```python
result = await client.classify_intent_llm(message, context)
```

**After:**
```python
result = await client.classify_intent(
    message="Tin nhắn tiếng Việt",
    context="IT Support"
)
# Returns: {"intent": "...", "confidence": 0.9, "reasoning": "..."}
```

### 5. Switching Models

**Before:** Not supported

**After:**
```python
# Switch to different model
client.set_model("phi3")

# Or use specific model per request
response = await client.complete(
    system_prompt="...",
    user_message="...",
    model="mistral"  # Temporary override
)
```

## Migration Steps

### Step 1: Install Ollama (Recommended for Vietnamese)

```bash
# Install Ollama
curl -fsSL https://ollama.com/install.sh | sh

# Pull Vietnamese-optimized model
ollama pull llama3

# Verify
ollama run llama3 "Xin chào"
```

### Step 2: Update .env

```bash
# .env file
LLM_MODEL=llama3
OLLAMA_BASE_URL=http://localhost:11434
```

### Step 3: Update Imports

In all files using the old LLM client:

```python
# Old
from src.llm import LLMClient, get_llm, LLMError

# New
from src.llm import MultiProviderLLMClient, get_llm_client, LLMError
```

### Step 4: Update Code

Replace all calls to `llm_client.complete()` with the new pattern:

```python
# Old
content, confidence = await llm.complete(system, user)

# New
response = await llm.complete(system, user)
content = response.content
```

### Step 5: Add Ollama Health Check (Optional)

```bash
# Check if Ollama is running
curl http://localhost:11434/api/tags
```

## Vietnamese Language Support

The new client is optimized for Vietnamese with:

1. **Auto-detect provider** from model name
2. **Built-in Vietnamese prompts** in intent classification
3. **Model registry** with Vietnamese-compatible models:
   - `llama3` - Best overall
   - `phi3` - Fast, efficient
   - `mistral` - Good multilingual
   - `qwen2` - Code + Vietnamese
   - `mixtral` - Highest quality

## Troubleshooting

### Ollama Connection Issues

```bash
# Check Ollama status
systemctl status ollama

# Or start manually
ollama serve

# Check port
netstat -tlnp | grep 11434
```

### Fallback to OpenAI

If Ollama is unavailable, the system can automatically use OpenAI:

```bash
# .env
OPENAI_API_KEY=sk-your-key
LLM_MODEL=gpt-4o  # Will use OpenAI instead
```

### Model Not Found

```bash
# Pull the model
ollama pull llama3

# List installed models
ollama list
```

## Performance Comparison

| Model | Provider | Speed | Quality | Cost |
|-------|----------|-------|---------|------|
| llama3 | Ollama | Fast (GPU) | High | Free |
| phi3 | Ollama | Very Fast | Medium | Free |
| gpt-4o | OpenAI | Fast | Very High | $0.005/1K |
| gpt-3.5 | OpenAI | Very Fast | Medium | $0.0005/1K |

## Testing

Run the test script:

```bash
cd /tmp/supervisor-api
python tests/test_ollama_provider.py
```

Expected output:
```
MULTI-PROVIDER LLM CLIENT TEST
============================================================
Available Models:
  llama3       | ollama    | Meta's latest, excellent multilingual...
  phi3         | ollama    | Microsoft's efficient model...
  mistral      | ollama    | Good multilingual...

✓ Ollama is available!
✓ Vietnamese: Bạn là AI được đào tạo bởi...
```
