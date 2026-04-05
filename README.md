# Multi-Agent Supervisor System

AI agent system with long-term memory for Microsoft Teams integration.

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
# Install dependencies
pip install -e .

# Copy environment file
cp .env.example .env
# Edit .env with your credentials

# Run the server
python -m src.api
```

## API Endpoints

- `POST /webhook/n8n` - Receive requests from n8n
- `POST /output/power-automate` - Send responses to Power Automate
- `GET /health` - Health check

## Environment Variables

| Variable | Description |
|----------|-------------|
| `DB_PASSWORD` | PostgreSQL password |
| `REDIS_PASSWORD` | Redis password |
| `WEBHOOK_INPUT_SECRET` | Secret for n8n webhook authentication |
| `POWER_AUTOMATE_WEBHOOK_URL` | Power Automate webhook URL |
