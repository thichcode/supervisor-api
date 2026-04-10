# Multi-Agent Supervisor System

[![Version](https://img.shields.io/badge/version-v1.2.3-blue.svg)](https://github.com/thichcode/supervisor-api/releases)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11+-orange.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.104+-cyan.svg)](https://fastapi.tiangolo.com/)

AI agent system with long-term memory for Microsoft Teams integration.

## Production Status

| Component | Status | Version |
|-----------|--------|---------|
| Core API | ✅ Production Ready | v1.2.3 |
| Multi-Provider LLM | ✅ Ollama/llama.cpp/OpenAI/Azure | v1.2.0+ |
| Circuit Breaker | ✅ Implemented | v1.0.0 |
| Dead Letter Queue | ✅ Implemented | v1.0.0 |
| Authentication | ✅ Implemented | v1.0.0 |
| Distributed Tracing | ✅ Implemented | v1.0.0 |
| Load Testing | ✅ Implemented | v1.0.0 |
| Prometheus Alerts | ✅ Implemented | v1.0.0 |
| External Memory Provider Registry | ✅ Multi-backend + Routed Prototype | v1.0.0+ |

**Production Readiness Score: 9.2/10** ✅

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
| GET | `/health` | Health check |
| GET | `/health/ready` | Readiness check |
| GET | `/metrics` | Prometheus metrics |

### Admin Endpoints (Require Auth)

| Method | Endpoint | Description |
|--------|----------|-------------|
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

## Load Testing

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

---

## Documentation

- [Changelog](./CHANGELOG.md) - Version history
- [Release Notes](./RELEASES/) - Detailed release notes
- [Configuration Guide](./CONFIGURATION_GUIDE.md) - Config precedence
- [API Documentation](./docs/api.md) - API reference

---

## License

MIT License - see [LICENSE](LICENSE) file for details.

---

## Support

- **Issues:** [GitHub Issues](https://github.com/thichcode/supervisor-api/issues)
- **Releases:** [GitHub Releases](https://github.com/thichcode/supervisor-api/releases)
