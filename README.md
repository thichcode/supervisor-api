# Multi-Agent Supervisor System

AI agent system with long-term memory for Microsoft Teams integration.

## Current Status

- Packaging/build has been fixed and `pip install .` now works.
- Test suite is currently passing: `15 passed`.
- Production hardening has been improved with:
  - dynamic confidence propagation in supervisor flow
  - stricter CORS configuration support
  - LLM readiness/health check support
  - timezone-aware datetime defaults in schemas/tests

### Production Readiness Snapshot

- **PoC / Demo:** Good
- **Staging:** Good
- **Production:** Improved, but not fully complete yet
- **Estimated score:** **8/10**

Main gaps before ~9/10 production-ready:
- integration tests with Postgres/Redis/API real runtime
- full end-to-end readiness/startup verification
- unified config source of truth between YAML and Python settings
- stronger failure-path testing for DB/Redis/LLM/network
- better cross-platform developer workflow (especially Makefile on Windows)

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

## Quick Start

```bash
# Install package
pip install .

# Run tests
python -m pytest -q

# Run the server
python -m src.api
```

> Note: the previous editable install path had packaging issues and has been corrected in `pyproject.toml`.

## API Endpoints

- `POST /webhook/n8n` - Receive requests from n8n
- `POST /output/power-automate` - Send responses to Power Automate
- `GET /health` - Health check
- `GET /health/ready` - Readiness check for DB, Redis, and LLM client state
- `GET /metrics` - Prometheus metrics endpoint

## Environment Variables

| Variable | Description |
|----------|-------------|
| `DB_PASSWORD` | PostgreSQL password |
| `REDIS_PASSWORD` | Redis password |
| `WEBHOOK_INPUT_SECRET` | Secret for n8n webhook authentication |
| `POWER_AUTOMATE_WEBHOOK_URL` | Power Automate webhook URL |
| `OPENAI_API_KEY` | API key for LLM client |

## Validation Commands

```bash
python -m pip install .
python -m pytest -q
python -m compileall src tests
```
