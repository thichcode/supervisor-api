# Multi-Agent Supervisor System

[![Version](https://img.shields.io/badge/version-v1.1.0-blue.svg)](https://github.com/thichcode/supervisor-api/releases)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11+-orange.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.104+-cyan.svg)](https://fastapi.tiangolo.com/)

AI agent system with long-term memory for Microsoft Teams integration. Designed for Vietnamese outsourcing companies with comprehensive intent classification and risk evaluation.

## Production Status

| Component | Status | Version |
|-----------|--------|---------|
| Core API | ✅ Production Ready | v1.1.0 |
| Multi-Provider LLM | ✅ Ollama/llama.cpp/OpenAI/Azure | v1.2.0+ |
| Knowledge Base | ✅ Policies/FAQs/Guides/Documents | v1.1.0 |
| Approval System | ✅ Confidence-based workflow | v1.0.0 |
| Circuit Breaker | ✅ Implemented | v1.0.0 |
| Dead Letter Queue | ✅ Implemented | v1.0.0 |
| Authentication | ✅ Implemented | v1.0.0 |
| Monitoring & Alerts | ✅ Dashboard/Health/Metrics | v1.1.0 |
| User/Config Management | ✅ CRUD Admin APIs | v1.1.0 |

**Production Readiness Score: 9.5/10** ✅

---

## Architecture

```
Microsoft Teams → Power Automate → n8n Webhook → Supervisor API
                                                      ↓
                                              Memory System
                                              (Postgres + Redis)
                                                      ↓
                                              Decision Engine
                                              ↓           ↓
                                    Direct Response  Subagents
                                                      ↓
                                    Context → Policy → Knowledge → Draft → QA
                                                      ↓
                                              LLM Service
                                    (Ollama/llama.cpp/OpenAI/Azure)
                                                      ↓
                                              Webhook Output
                                              ↓
                                         Power Automate
```

---

## LLM Provider Support

### Supported Providers

| Provider | Config | Description |
|----------|--------|-------------|
| **Ollama** (Recommended) | `LLM_PROVIDER=ollama` | Self-hosted, Vietnamese-optimized |
| **llama.cpp Server** | `LLM_PROVIDER=ollama` + custom URL | OpenAI-compatible API |
| **OpenAI** | `LLM_PROVIDER=openai` | GPT-4, GPT-3.5 |
| **Azure OpenAI** | `LLM_PROVIDER=azure` | Enterprise deployment |

### Quick Setup with llama.cpp

```bash
# Start llama.cpp server
./llama-server \
    -m models/llama-3.1-8b-instruct-q4_k_m.gguf \
    --host 0.0.0.0 \
    --port 8080 \
    -ngl 32

# Configure supervisor-api
LLM_PROVIDER=ollama
LLM_MODEL=llama3
OLLAMA_BASE_URL=http://llama-cpp:8080
OLLAMA_TIMEOUT=320
```

---

## Quick Start

### Docker Compose (Recommended)

```bash
# Clone and configure
git clone https://github.com/thichcode/supervisor-api.git
cd supervisor-api

# Create .env file
cp .env.example .env
# Edit .env with your configuration

# Start all services (includes Ollama/llama.cpp)
docker-compose up -d

# Check status
docker-compose ps

# View logs
docker-compose logs -f supervisor-api
```

### Local Development

```bash
# Install package
pip install -e ".[dev]"

# Run tests
python -m pytest -q

# Run the server
python -m src.api.app
```

---

## Environment Variables

### Required

| Variable | Description |
|----------|-------------|
| `DB_PASSWORD` | PostgreSQL password |
| `REDIS_PASSWORD` | Redis password |
| `WEBHOOK_INPUT_SECRET` | Webhook authentication secret |
| `POWER_AUTOMATE_WEBHOOK_URL` | Power Automate callback URL |

### LLM Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `LLM_PROVIDER` | `ollama` | Provider: `ollama`, `openai`, `azure` |
| `LLM_MODEL` | `llama3` | Model name |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama/llama.cpp server URL |
| `OLLAMA_TIMEOUT` | `320` | Request timeout in seconds |
| `LLM_TEMPERATURE` | `0.7` | Generation temperature |
| `LLM_MAX_TOKENS` | `2000` | Max tokens per response |
| `OPENAI_API_KEY` | - | OpenAI API key (optional) |

### Optional - OpenTelemetry

| Variable | Default | Description |
|----------|---------|-------------|
| `OTEL_EXPORTER_OTLP_ENDPOINT` | Console only | OTLP collector endpoint |
| `OTEL_SERVICE_NAME` | supervisor-api | Service name in traces |

### Optional - Circuit Breaker

| Variable | Default | Description |
|----------|---------|-------------|
| `CIRCUIT_BREAKER_FAILURE_THRESHOLD` | 5 | Failures before opening |
| `CIRCUIT_BREAKER_SUCCESS_THRESHOLD` | 2 | Successes to close |
| `CIRCUIT_BREAKER_TIMEOUT` | 30 | Seconds before half-open |

### Optional - External Memory (MemPalace)

| Variable | Default | Description |
|----------|---------|-------------|
| `MEMPALACE_ENABLED` | `false` | Enable MemPalace adapter |
| `MEMPALACE_PATH` | empty | Path to local MemPalace palace |
| `MEMPALACE_TOP_K` | `3` | Number of external memory hits |

---

## API Endpoints

### Public Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/webhook/n8n` | Receive requests from n8n |
| POST | `/chat` | Direct user chat |
| POST | `/system/query` | Query user/case info |
| POST | `/guide/deliver` | Deliver guideline to user |
| POST | `/callback/send` | Send async callback |
| GET | `/health` | Health check |
| GET | `/health/ready` | Readiness check |
| GET | `/health/detailed` | Detailed system stats |
| GET | `/metrics` | Prometheus metrics |
| GET | `/metrics/dashboard` | Dashboard metrics (JSON) |
| GET | `/metrics/dashboard/html` | Dashboard metrics (HTML with charts) |

### Approval Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/approvals` | List all approvals |
| GET | `/approvals/{id}` | Get approval details |
| POST | `/approvals/{id}/action` | Approve or reject |

### Knowledge Base Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/knowledge/stats` | KB statistics |
| POST | `/knowledge/search` | Search KB |
| POST | `/knowledge/search/enhanced` | LLM-enhanced search |
| POST | `/knowledge/bulk-import` | Bulk import KB |
| POST/GET/PUT/DELETE | `/knowledge/policies/{id}` | Policy CRUD |
| POST/GET/PUT/DELETE | `/knowledge/faqs/{id}` | FAQ CRUD |
| POST/GET/PUT/DELETE | `/knowledge/guides/{id}` | Guide CRUD |
| POST/GET/PUT/DELETE | `/knowledge/documents/{id}` | Document CRUD |

### Alert Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/alerts` | Create alert |
| GET | `/alerts` | List alerts |
| PUT | `/alerts/{id}/acknowledge` | Acknowledge alert |
| DELETE | `/alerts/{id}` | Delete alert |

### Admin Endpoints (Require Auth)

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST/GET/PUT/DELETE | `/admin/users` | User CRUD |
| POST/GET/PUT/DELETE | `/admin/config` | Config CRUD |
| GET | `/admin/errors/dlq` | List DLQ entries |
| POST | `/admin/errors/dlq/{id}/retry` | Retry failed message |
| DELETE | `/admin/errors/dlq/{id}` | Delete DLQ entry |
| GET | `/admin/errors/circuit-breakers` | View CB status |

---

## Production Features

### Reliability

| Feature | Description |
|---------|-------------|
| Circuit Breaker | Auto-prevents cascading failures |
| Dead Letter Queue | Zero message loss with retry |
| Error Handling | Structured errors, no leakage |

### Security

| Feature | Description |
|---------|-------------|
| JWT Auth | User/Admin authentication |
| HMAC Verification | Webhook signature validation |
| API Keys | Service-to-service auth |
| RBAC | Role-based access control |

### Observability

| Feature | Description |
|---------|-------------|
| OpenTelemetry | Distributed tracing |
| Prometheus Metrics | Full metrics coverage |
| Alert Rules | Proactive monitoring |
| Health Checks | Dependency awareness |

---

## Intent Classification

The system uses comprehensive keyword patterns for Vietnamese outsourcing companies:

| Intent | Keywords (sample) |
|--------|-------------------|
| **FAQ** | "là gì", "như thế nào", "cái gì", "cách làm" |
| **POLICY** | "quy định", "chính sách", "hướng dẫn", "quy trình", "nghỉ phép" |
| **SUPPORT_CASE** | "lỗi", "hỏng", "không được", "cần hỗ trợ", "bị lỗi" |
| **ANALYSIS** | "phân tích", "báo cáo", "thống kê", "số liệu" |
| **EXECUTIVE** | "sếp", "giám đốc", "gấp", "khẩn", "doanh thu" |

Role-based context boost: Project Manager → Analysis, HR → Policy, IT → Support

```bash
# Quick test
./load_test/quick_test.sh

# Full load test with k6
k6 run \
  --env BASE_URL=https://api.example.com \
  --env WEBHOOK_SECRET=*** \
  load_test/k6_load.js
```

---

## Monitoring

### Prometheus Metrics

Access at `GET /metrics`:

| Metric | Type | Description |
|--------|------|-------------|
| `supervisor_requests_total` | Counter | Total requests |
| `supervisor_request_duration_seconds` | Histogram | Request latency |
| `supervisor_errors_total` | Counter | Total errors |
| `supervisor_llm_calls_total` | Counter | LLM invocations |
| `supervisor_llm_cost_usd` | Counter | LLM cost |
| `supervisor_circuit_breaker_state` | Gauge | CB state (0/1/2) |
| `supervisor_dlq_pending_total` | Gauge | Pending DLQ messages |

### Dashboard Metrics

Access at `GET /metrics/dashboard` (JSON) or `GET /metrics/dashboard/html` (HTML with charts):

```json
{
  "overview": {
    "total_approvals": 15,
    "auto_sent": 0,
    "need_manual_review": 15,
    "auto_send_rate": 0.0
  },
  "approvals": {
    "pending": 9,
    "approved": 6,
    "rejected": 0,
    "approve_rate": 100.0
  },
  "ai_quality": {
    "avg_confidence": 43.5,
    "high_confidence_count": 0,
    "low_confidence_count": 15
  },
  "user_satisfaction": {
    "total_votes": 1,
    "agree": 1,
    "satisfaction_rate": 100.0
  }
}
```

**HTML Dashboard Features:**
- Dark theme with glassmorphism cards
- Chart.js for visualizations (doughnut charts)
- Confidence bar with threshold indicator (90%)
- User satisfaction voting bar
- Responsive design

---
## Documentation
- [Changelog](./CHANGELOG.md) - Version history
- [Release Notes](./RELEASES/) - Detailed release notes
- [Configuration Guide](./CONFIGURATION_GUIDE.md) - Config precedence
- [API Documentation](./docs/api.md) - Full API reference
- [Deploy Guide](./DEPLOY_GUIDE.md) - Production deployment

---

## License

MIT License - see [LICENSE](LICENSE) file for details.

---

## Support

- **Issues:** [GitHub Issues](https://github.com/thichcode/supervisor-api/issues)
- **Releases:** [GitHub Releases](https://github.com/thichcode/supervisor-api/releases)
