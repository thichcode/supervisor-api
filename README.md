# Multi-Agent Supervisor System

[![Version](https://img.shields.io/badge/version-v1.0.0-blue.svg)](https://github.com/thichcode/supervisor-api/releases)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10+-orange.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.104+-cyan.svg)](https://fastapi.tiangolo.com/)

AI agent system with long-term memory for Microsoft Teams integration.

## Production Status

| Component | Status | Version |
|-----------|--------|---------|
| Core API | ✅ Production Ready | v1.0.0 |
| Circuit Breaker | ✅ Implemented | v1.0.0 |
| Dead Letter Queue | ✅ Implemented | v1.0.0 |
| Authentication | ✅ Implemented | v1.0.0 |
| Distributed Tracing | ✅ Implemented | v1.0.0 |
| Load Testing | ✅ Implemented | v1.0.0 |
| Prometheus Alerts | ✅ Implemented | v1.0.0 |

**Production Readiness Score: 9/10** ✅

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
                                              Webhook Output
                                              ↓
                                         Power Automate
```

---

## Quick Start

```bash
# Install package
pip install .

# Run tests
python -m pytest -q

# Run the server
python -m src.api
```

---

## Production Features (v1.0.0)

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

### Operations

| Feature | Description |
|---------|-------------|
| Load Testing | k6 + Locust scripts |
| Docker Compose | One-command deployment |
| Prometheus Alerts | SLO/SLA monitoring |

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

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `DB_PASSWORD` | Yes | PostgreSQL password |
| `REDIS_PASSWORD` | Yes | Redis password |
| `WEBHOOK_INPUT_SECRET` | Yes | Webhook authentication secret |
| `POWER_AUTOMATE_WEBHOOK_URL` | Yes | Power Automate callback URL |
| `OPENAI_API_KEY` | Yes | Azure OpenAI API key |
| `JWT_SECRET` | No | JWT signing secret (auto-generated if not set) |

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

---

## Deployment

### Docker Compose (Recommended)

```bash
# Production deployment
docker-compose up -d

# Check status
docker-compose ps

# View logs
docker-compose logs -f supervisor-api
```

### Kubernetes (Helm - Coming Soon)

```bash
helm install supervisor-api ./charts/supervisor-api
```

---

## Load Testing

```bash
# Quick test
./load_test/quick_test.sh

# Full load test with k6
k6 run \
  --env BASE_URL=https://api.example.com \
  --env WEBHOOK_SECRET=$WEBHOOK_SECRET \
  load_test/k6_load.js

# Alternative with Locust
locust -f load_test/locustfile.py --host=http://localhost:8000
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

### Alert Examples

```yaml
# High error rate
- alert: HighErrorRate
  expr: rate(supervisor_errors_total[5m]) / rate(supervisor_requests_total[5m]) > 0.05
  severity: critical

# Circuit breaker open
- alert: CircuitBreakerOpen
  expr: supervisor_circuit_breaker_state != 0
  for: 1m
  severity: warning
```

---

## Documentation

- [Changelog](./CHANGELOG.md) - Version history
- [Release Notes](./RELEASES/) - Detailed release notes
- [API Documentation](./docs/api.md) - API reference
- [Deployment Guide](./docs/deployment.md) - Production deployment
- [Monitoring Guide](./docs/monitoring.md) - Observability setup

---

## License

MIT License - see [LICENSE](LICENSE) file for details.

---

## Support

- **Issues:** [GitHub Issues](https://github.com/thichcode/supervisor-api/issues)
- **Releases:** [GitHub Releases](https://github.com/thichcode/supervisor-api/releases)
