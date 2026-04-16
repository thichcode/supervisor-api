# Multi-Agent Supervisor System

[![Version](https://img.shields.io/badge/version-v1.2.0-blue.svg)](https://github.com/thichcode/supervisor-api/releases)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11+-orange.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.104+-cyan.svg)](https://fastapi.tiangolo.com/)

AI agent system with long-term memory for Microsoft Teams integration. Designed for Vietnamese outsourcing companies with comprehensive intent classification and risk evaluation.

## Production Status

| Component | Status | Version |
|-----------|--------|---------|
| Core API | ✅ Production Ready | v1.2.0 |
| SimpleAgent | ✅ Unified agent (1 call) | v1.2.0 |
| Pattern Learning | ✅ Learn from approvals | v1.2.0 |
| Multi-Provider LLM | ✅ Ollama/Azure/OpenAI | v1.2.0+ |
| Knowledge Base | ✅ Policies/FAQs/Guides/Documents | v1.1.0 |
| Approval System | ✅ Telegram + Power Automate | v1.2.0 |
| Circuit Breaker | ✅ Implemented | v1.0.0 |
| Dead Letter Queue | ✅ Implemented | v1.0.0 |

**Production Readiness Score: 9.5/10** ✅

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         MS TEAMS (User/Workflow Bot)                        │
└────────────────────────────────┬────────────────────────────────────────────┘
                                 │ Power Automate / n8n
                                 ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         Supervisor API                                      │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────────────────┐    │
│  │ Memory      │→ │ SimpleAgent │→ │ Pattern Match (>90%)?           │    │
│  │ Service     │  │ (1 call)   │  │ YES → Use stored answer         │    │
│  └─────────────┘  └─────────────┘  │ NO → LLM generate               │    │
│                                     └─────────────────────────────────┘    │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │ Confidence < 30%? → Approval Required → Telegram Notification       │   │
│  │ Manager approves → Store Q&A Pattern → Next time match directly     │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
└────────────────────────────────┬────────────────────────────────────────────┘
                                 │
                                 ▼ Power Automate
                          Response to Teams
```

### Key Features

1. **SimpleAgent** - Unified agent, 1 LLM call (replaces 5-agent pipeline)
2. **Pattern Learning** - Learn from approved responses, auto-match similar questions
3. **Telegram Approval** - Managers approve via Telegram inline buttons

---

## Quick Start

### 1. Clone and Configure

```bash
git clone https://github.com/thichcode/supervisor-api.git
cd supervisor-api
cp .env.example .env
```

### 2. Edit .env

```bash
# Database
DB_PASSWORD=xxx

# Redis
REDIS_PASSWORD=xxx

# Webhook
WEBHOOK_INPUT_SECRET=xxx
POWER_AUTOMATE_WEBHOOK_URL=https://xxx.azure.com/workflows/xxx

# Telegram (for approval)
TELEGRAM_BOT_TOKEN=xxx
TELEGRAM_APPROVAL_CHAT_IDS=xxx
```

### 3. Deploy with Docker Compose

```bash
# Start all services (API + Postgres + Redis + Ollama + Monitoring)
docker-compose up -d

# Pull LLM model (run once)
docker exec supervisor-ollama ollama pull llama3.1

# Check status
docker-compose ps

# View logs
docker-compose logs -f supervisor
```

### 4. Create Database Tables

```bash
# Run migrations
psql "$DATABASE_URL" -f migrations/20260415_01_learning_hardening.up.sql

# Or, if your deployment has Alembic configured separately
# alembic upgrade head
```

### 5. Services

| Service | URL | Description |
|---------|-----|-------------|
| API | http://localhost:8000 | Supervisor API |
| API Docs | http://localhost:8000/docs | Swagger UI |
| Prometheus | http://localhost:9090 | Metrics |
| Grafana | http://localhost:3000 | Dashboards |
| Ollama | http://localhost:11434 | LLM API |
pip install -r requirements.txt
python -m src.api.app
```

---

## LLM Configuration

### Option 1: Ollama (Free, Recommended)

```bash
# Install Ollama: https://ollama.com
ollama pull llama3.1    # 8GB RAM - Best for Vietnamese
ollama pull qwen2:7b    # 6GB RAM - Alibaba, good multilingual

# .env
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://localhost:11434
LLM_MODEL=llama3.1
```

### Option 2: Azure OpenAI

```bash
# .env
LLM_PROVIDER=azure
AZURE_OPENAI_ENDPOINT=https://xxx.openai.azure.com
AZURE_OPENAI_KEY=xxx
AZURE_DEPLOYMENT_NAME=gpt-4o-mini
```

### Option 3: OpenAI

```bash
# .env
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-xxx
LLM_MODEL=gpt-4o-mini
```

### Model Comparison for Vietnamese

| Model | Cost | Vietnamese | Recommended |
|-------|------|-----------|-------------|
| **llama3.1** | Free | ⭐⭐⭐⭐ | ✅ Best choice |
| **qwen2:7b** | Free | ⭐⭐⭐⭐ | ✅ Good, light |
| **gpt-4o-mini** | $ | ⭐⭐⭐⭐⭐ | ✅ If budget available |

---

## Pattern Learning

When you approve a response, it's stored and matched against future similar questions.

### Flow

```
1. User asks "Cách reset mật khẩu?"
2. System generates response, confidence < 30%
3. Manager approves via Telegram
4. Q&A stored in response_patterns table
5. Next user asks "Làm sao đổi password?"
6. Pattern match: 92% similarity
7. → Use stored answer immediately (no LLM call)
```

### Database Migration

```bash
# Create table
alembic revision --autogenerate -m "Add ResponsePattern table"
alembic upgrade head
```

---

## Approval System

### Telegram Setup

1. Create Telegram Bot: @BotFather → `/newbot`
2. Copy Bot Token
3. Get Chat ID: Start chat with bot, then use @userinfobot
4. Add to `.env`:

```bash
TELEGRAM_BOT_TOKEN=123456:ABC-DEF...
TELEGRAM_APPROVAL_CHAT_IDS=123456789
```

### Approval Flow

```
Confidence < 30% → Create approval → Send to Telegram
                                    ├── ✅ Approve → Store pattern → Send to Teams
                                    ├── 🚫 Reject → Log → Send rejection
                                    └── 🔍 Search KB → Enter keywords → New response
```

### KB Search Button

When reviewing an approval, click **🔍 Search KB** to:
1. Enter keywords to search Knowledge Base
2. System finds relevant KB articles
3. Generate new response based on KB results
4. Shows new response for approval

---

## API Endpoints

### Chat

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/chat` | Send message |
| POST | `/webhook/n8n` | Webhook from n8n/Power Automate |

### Approval

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/approvals` | List approvals |
| GET | `/approvals/{id}` | Get approval |
| POST | `/approvals/{id}/action` | Approve/Reject |

### Knowledge Base

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/knowledge/stats` | KB statistics |
| POST | `/knowledge/search` | Search KB |
| POST/GET/PUT/DELETE | `/knowledge/policies/{id}` | Policy CRUD |
| POST/GET/PUT/DELETE | `/knowledge/faqs/{id}` | FAQ CRUD |
| POST/GET/PUT/DELETE | `/knowledge/guides/{id}` | Guide CRUD |

### Monitoring

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | Health check |
| GET | `/metrics` | Prometheus metrics |
| GET | `/metrics/dashboard` | Dashboard JSON |

---

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `DB_PASSWORD` | Yes | PostgreSQL password |
| `REDIS_PASSWORD` | Yes | Redis password |
| `WEBHOOK_INPUT_SECRET` | Yes | Webhook authentication |
| `POWER_AUTOMATE_WEBHOOK_URL` | Yes | Teams callback URL |
| `LLM_PROVIDER` | Yes | `ollama`, `openai`, `azure` |
| `LLM_MODEL` | Yes | Model name |
| `OLLAMA_BASE_URL` | No | Ollama server URL |
| `TELEGRAM_BOT_TOKEN` | No | Telegram bot token |
| `TELEGRAM_APPROVAL_CHAT_IDS` | No | Comma-separated chat IDs |

---

## Testing

```bash
# Run all tests
python -m pytest -q

# Run specific test
python -m pytest tests/test_xxx.py -v
```

**Current: 170 tests passing**

---

## File Structure

```
supervisor-api/
├── src/
│   ├── api/
│   │   ├── app.py              # FastAPI entry point
│   │   └── routers/           # API endpoints
│   ├── agents/
│   │   ├── simple_agent.py     # Unified agent (v1.2)
│   │   └── subagents.py        # Legacy agents
│   ├── core/
│   │   ├── supervisor.py       # Main orchestrator
│   │   └── approval.py         # Approval system
│   ├── services/
│   │   ├── chat_service.py     # Chat handling
│   │   ├── feedback_service.py # Feedback handling
│   │   └── pattern_learning_service.py  # Pattern learning
│   ├── db/
│   │   └── models.py           # SQLAlchemy models
│   └── llm/
│       └── provider.py         # Multi-provider LLM
├── tests/                      # Test suite
├── .env.example               # Environment template
└── requirements.txt           # Dependencies
```

---

## Documentation

- [Changelog](./CHANGELOG.md) - Version history
- [API Documentation](./docs/api.md) - Full API reference
- [Deploy Guide](./DEPLOY_GUIDE.md) - Production deployment

---

## License

MIT License

---

## Support

- **Issues:** [GitHub Issues](https://github.com/thichcode/supervisor-api/issues)
- **Releases:** [GitHub Releases](https://github.com/thichcode/supervisor-api/releases)
