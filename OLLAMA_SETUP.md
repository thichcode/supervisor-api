# Ollama Setup for Supervisor-API

## Vietnamese Language Models

### Quick Install Ollama

```bash
# Install Ollama (Linux/macOS)
curl -fsSL https://ollama.com/install.sh | sh

# Install on Windows (WSL2 recommended)
# Or download from: https://ollama.com/download
```

### Pull Vietnamese-Optimized Models

```bash
# Recommended models for Vietnamese

# 1. Llama 3 - Best overall for Vietnamese
ollama pull llama3

# 2. Llama 3.1 - Extended context (128K)
ollama pull llama3.1

# 3. Phi-3 - Fast, efficient
ollama pull phi3

# 4. Mistral - Good multilingual
ollama pull mistral

# 5. Qwen 2 - Alibaba, excellent multilingual
ollama pull qwen2

# 6. Mixtral - Mixture of experts, high quality
ollama pull mixtral
```

### Start Ollama Service

```bash
# Start as service (background)
systemctl start ollama
systemctl enable ollama

# Or start manually
ollama serve

# Verify
curl http://localhost:11434/api/tags
```

### RAM Requirements

| Model | RAM Required | Best For |
|-------|-------------|----------|
| phi3 | 4GB | Fast responses, simple tasks |
| llama3 | 8GB | Balanced quality/speed |
| mistral | 6GB | Good multilingual |
| qwen2 | 6GB | Code + Vietnamese |
| llama3.1 | 8GB | Long documents |
| mixtral | 12GB | Highest quality |

### Verify Vietnamese Support

```bash
ollama run llama3 "Xin chào, bạn tên gì?"

# Expected output: Vietnamese response
```

---

## Configuration

### Environment Variables

```bash
# .env file

# Ollama (default - for Vietnamese)
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_DEFAULT_MODEL=llama3
OLLAMA_TIMEOUT=120

# Model selection
LLM_MODEL=llama3
LLM_TEMPERATURE=0.7
LLM_MAX_TOKENS=2000

# OpenAI (fallback - optional)
# OPENAI_API_KEY=sk-...
# LLM_MODEL=gpt-4o
```

### Docker Deployment

```yaml
# docker-compose.yml
services:
  ollama:
    image: ollama/ollama:latest
    ports:
      - "11434:11434"
    volumes:
      - ollama_data:/root/.ollama
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: all
              capabilities: [gpu]

  supervisor-api:
    depends_on:
      - ollama
    environment:
      - OLLAMA_BASE_URL=http://ollama:11434
      - LLM_MODEL=llama3

volumes:
  ollama_data:
```

---

## API Usage

### Health Check

```bash
curl http://localhost:11434/api/tags
```

### Direct Chat

```bash
curl http://localhost:11434/api/chat -d '{
  "model": "llama3",
  "messages": [
    {"role": "user", "content": "Xin chào bạn"}
  ]
}'
```

### Model Management

```bash
# List installed models
ollama list

# Remove model
ollama rm llama3

# Copy model (for customization)
ollama cp llama3 my-custom-llama
```

---

## Troubleshooting

### Out of Memory

```bash
# Check GPU memory
nvidia-smi

# Use smaller model
ollama rm llama3
ollama pull phi3

# Or use CPU mode (slower)
OLLAMA_HOST=http://localhost:11434
```

### Connection Refused

```bash
# Check if Ollama is running
systemctl status ollama

# Or start manually
ollama serve

# Check listening port
netstat -tlnp | grep 11434
```

### Slow Responses

```bash
# Use GPU
nvidia-smi

# Or use faster model
ollama pull phi3
```

---

## Performance Tips

1. **Use GPU** - Ollama with GPU is 10-50x faster
2. **Batch requests** - Group similar requests
3. **Cache responses** - Redis caching for repeated queries
4. **Use appropriate model**:
   - Simple FAQ → phi3 (fast)
   - Complex analysis → llama3/mixtral
   - Long documents → llama3.1
