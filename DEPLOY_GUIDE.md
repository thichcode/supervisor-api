# Deployment Guide

## Quick Start (Development)

```bash
# 1. Clone & enter
git clone https://github.com/thichcode/supervisor-api.git
cd supervisor-api

# 2. Create virtual environment
python -m venv venv
source venv/bin/activate  # Linux/Mac
# venv\Scripts\activate   # Windows

# 3. Install dependencies
pip install -e .

# 4. Setup environment
cp .env.example .env
# Edit .env with your settings

# 5. Start PostgreSQL & Redis (Docker)
docker run -d --name supervisor-postgres \
  -e POSTGRES_DB=supervisor_db \
  -e POSTGRES_USER=postgres \
  -e POSTGRES_PASSWORD=your_password \
  -p 5432:5432 postgres:16

docker run -d --name supervisor-redis \
  -p 6379:6379 redis:7

# 6. Start llama.cpp (separate terminal)
# Download model first: https://huggingface.co/bartowski/Llama-3.1-8B-Instruct-GGUF
./llama.cpp/server -m Llama-3.1-8B-Q4_K_M.gguf -c 8192 -port 8088

# 7. Run the app
python -m src.api.app
# Or: uvicorn src.api.app:app --host 0.0.0.0 --port 8000 --reload
```

---

## Production Deployment

### 1. Prerequisites

| Component | Version | Notes |
|-----------|---------|-------|
| Python | 3.11+ | Required |
| PostgreSQL | 16+ | Database |
| Redis | 7+ | Cache/Session |
| llama.cpp | latest | LLM inference (or Ollama) |
| Docker | 24+ | Optional, for containerized deploy |

### 2. Environment Variables

Create `.env` file:

```bash
# Source of truth: src/config.py

# ============ APP ============
APP_ENV=production
APP_DEBUG=false
APP_HOST=0.0.0.0
APP_PORT=8000
APP_WORKERS=4

# ============ DATABASE ============
DB_HOST=localhost
DB_PORT=5432
DB_NAME=supervisor_db
DB_USER=postgres
DB_PASSWORD=change_this_password

# ============ REDIS ============
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_PASSWORD=

# ============ LLM PROVIDER ============
# Options: ollama, openai, azure
LLM_PROVIDER=ollama

# Option A: Ollama (self-hosted, recommended for Vietnamese)
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_DEFAULT_MODEL=llama3
OLLAMA_TIMEOUT=320
OLLAMA_IMAGE_MODEL=llama3.1-vision
LLM_HEALTHCHECK_ENABLED=false

# Option B: llama.cpp (direct HTTP)
# LLM_PROVIDER=ollama
# OLLAMA_BASE_URL=http://localhost:8088
# OLLAMA_TIMEOUT=320

# Option C: OpenAI
# OPENAI_API_KEY=sk-...

# Option D: Azure OpenAI
# AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com/
# AZURE_OPENAI_KEY=your-key
# AZURE_DEPLOYMENT_NAME=gpt-4

# ============ SECURITY ============
WEBHOOK_INPUT_SECRET=your_webhook_secret
JWT_SECRET=your_jwt_secret
HMAC_SECRET=your_hmac_secret
API_KEYS=key1,key2

# ============ OUTPUT ============
POWER_AUTOMATE_WEBHOOK_URL=https://prod-...-logic-.azure.com/
WEBHOOK_TIMEOUT=30

# ============ REASONING / TOOLS ============
ENABLE_TOOLS=true
ENABLE_REASONING_LOOP=false
REASONING_LOOP_MAX_ITERATIONS=3
REASONING_LOOP_TOOL_RETRY=1
ENABLE_LLM_TOOL_PLANNING=false
REASONING_LOOP_ROLLOUT_USER_PERCENT=100
REASONING_LOOP_ROLLOUT_TEAM_PERCENT=100
REASONING_LOOP_ROLLOUT_SALT=reasoning-loop-v1

# ============ MEMORY ============
MEMORY_CONVERSATION_TTL=86400
MEMORY_SUMMARY_TTL=604800
MEMORY_MAX_TOKENS=4000
MEMPALACE_ENABLED=false
MEMPALACE_PATH=
MEMPALACE_TOP_K=3
FILE_MEMORY_ENABLED=false
FILE_MEMORY_PATH=

# ============ TELEGRAM ============
TELEGRAM_BOT_TOKEN=
TELEGRAM_APPROVAL_CHAT_IDS=
TELEGRAM_PARSE_MODE=Markdown
APPROVAL_NOTIFICATION_COOLDOWN_SECONDS=0

# ============ OPTIONAL: N8N ============
N8N_BASE_URL=http://localhost:5678
# N8N_API_KEY=your_n8n_api_key
N8N_WEBHOOK_SECRET=

# ============ OPTIONAL: MONITORING ============
LOG_LEVEL=INFO
```

For a complete, up-to-date variable list, always cross-check:
- `src/config.py` (runtime authority)
- `.env.example` (development baseline)
- `.env.production.example` (production baseline)

### 3. Database Setup

```sql
-- Create database
CREATE DATABASE supervisor_db;

-- Run migrations (if using Alembic)
alembic upgrade head
```

Or use Docker:
```bash
docker run -d --name supervisor-postgres \
  -e POSTGRES_DB=supervisor_db \
  -e POSTGRES_USER=postgres \
  -e POSTGRES_PASSWORD=your_password \
  -v postgres_data:/var/lib/postgresql/data \
  -p 5432:5432 postgres:16
```

### 4. LLM Setup

#### Option A: Ollama (Recommended)

```bash
# Install Ollama
curl -fsSL https://ollama.com/install.sh | sh

# Start Ollama
ollama serve

# Pull model
ollama pull llama3
# Or: ollama pull llama3:8b-instruct-q4_K_M

# Verify
ollama list
```

#### Option B: llama.cpp (Direct)

```bash
# 1. Download llama.cpp binary
git clone https://github.com/ggerganov/llama.cpp.git
cd llama.cpp
make

# 2. Download model (GGUF format)
# https://huggingface.co/bartowski/Llama-3.1-8B-Instruct-GGUF
# Or: https://huggingface.co/TheBloke/Llama-3.1-8B-Instruct-GGUF

# 3. Start server
./llama.cpp/server \
  -m Llama-3.1-8B-Q4_K_M.gguf \
  -c 8192 \
  -port 8088 \
  --host 0.0.0.0

# Test
curl http://localhost:8088/v1/models
```

### 5. Running the App

#### Development
```bash
python -m src.api.app
# OR
uvicorn src.api.app:app --reload --host 0.0.0.0 --port 8000
```

#### Production (with Gunicorn)
```bash
# Install gunicorn
pip install gunicorn

# Run with workers
gunicorn src.api.app:app \
  --workers 4 \
  --worker-class uvicorn.workers.UvicornWorker \
  --bind 0.0.0.0:8000 \
  --timeout 120 \
  --access-logfile - \
  --error-logfile -
```

#### Docker

```yaml
# docker-compose.yml
version: '3.8'

services:
  app:
    build: .
    ports:
      - "8000:8000"
    environment:
      - DB_HOST=postgres
      - DB_PASSWORD=postgres
      - REDIS_HOST=redis
      - LLM_PROVIDER=ollama
      - OLLAMA_BASE_URL=http://host.docker.internal:11434
    depends_on:
      - postgres
      - redis
    extra_hosts:
      - "host.docker.internal:host-gateway"

  postgres:
    image: postgres:16
    environment:
      POSTGRES_DB: supervisor_db
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: postgres
    volumes:
      - postgres_data:/var/lib/postgresql/data
    ports:
      - "5432:5432"

  redis:
    image: redis:7
    ports:
      - "6379:6379"

  ollama:
    image: ollama/ollama:latest
    ports:
      - "11434:11434"
    volumes:
      - ollama_data:/root/.ollama
```

### 6. Health Check

```bash
# Basic health
curl http://localhost:8000/health

# Detailed health
curl http://localhost:8000/health/detailed

# Should return:
# {"status": "healthy", "components": {...}}
```

---

## Environment-Specific Notes

### Linux Server (24GB RAM, 4 cores, CPU only)

```bash
# Recommended: use llama.cpp with Q4 quantization
# Model: Llama-3.1-8B-Q4_K_M.gguf (~5GB RAM)

# Start llama.cpp
./llama.cpp/server -m Llama-3.1-8B-Q4_K_M.gguf -c 8192 -port 8088 --threads 4

# In .env:
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://localhost:8088
OLLAMA_TIMEOUT=320
```

### Docker Networking (Important!)

If running supervisor in Docker and llama.cpp on host:

```bash
# Option 1: Use host.docker.internal
# In .env inside container:
OLLAMA_BASE_URL=http://host.docker.internal:8088

# Option 2: Add to docker-compose.yml
extra_hosts:
  - "host.docker.internal:host-gateway"

# Option 3: Run both in Docker
# Add llama.cpp container to docker-compose
```

---

## Troubleshooting

### "Connection refused" to LLM

1. Check if Ollama/llama.cpp is running:
   ```bash
   curl http://localhost:11434/api/tags  # Ollama
   curl http://localhost:8088/v1/models  # llama.cpp
   ```

2. Check firewall:
   ```bash
   sudo ufw allow 11434/tcp
   sudo ufw allow 8088/tcp
   ```

### Database connection failed

```bash
# Check PostgreSQL
docker logs supervisor-postgres

# Test connection
psql -h localhost -U postgres -d supervisor_db
```

### Out of memory

```bash
# Check RAM
free -h

# Use smaller model or quantization
# Q4_K_M = 4.7GB, Q5_K_S = 5.5GB, Q8_0 = 7.3GB
```

---

## Next Steps

After deployment, see:
- `ADMIN_GUIDE.md` - Operations, monitoring, maintenance
- `SRS.md` - System requirements specification
- `FLOW.md` - Architecture and data flow