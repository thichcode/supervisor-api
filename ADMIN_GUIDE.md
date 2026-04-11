# Admin Guide

## Overview

This guide covers operational tasks for the Multi-Agent Supervisor System:
- Monitoring & Health
- Logs & Metrics
- Backup & Restore
- Performance Tuning
- Troubleshooting
- Maintenance

---

## 1. Monitoring & Health

### Health Endpoints

```bash
# Basic health check
curl http://localhost:8000/health
# Response: {"status": "healthy"}

# Detailed health with component status
curl http://localhost:8000/health/detailed

# Response includes:
# - database: PostgreSQL connection
# - redis: Redis connection  
# - llm: LLM provider availability
# - n8n: n8n connector status (if configured)
```

### Component Status

| Endpoint | Purpose |
|----------|---------|
| `/health` | Basic liveness probe |
| `/health/detailed` | Full system status |
| `/metrics` | Prometheus metrics (if enabled) |

---

## 2. Logs

### Log Levels

Set via `LOG_LEVEL` env var:
- `DEBUG` - Verbose, includes all details
- `INFO` - Normal operations (default)
- `WARNING` - Warnings only
- `ERROR` - Errors only

### Log Structure

Logs are structured JSON:
```json
{
  "event": "supervisor.process",
  "request_id": "abc123",
  "intent": "support_case",
  "risk_level": "low",
  "agents_used": ["context", "policy", "knowledge", "draft", "qa"],
  "processing_time_ms": 3450,
  "timestamp": "2026-04-11T22:30:00Z"
}
```

### Viewing Logs

```bash
# Docker
docker logs -f supervisor-api

# Local (journal)
journalctl -u supervisor -f

# File (if configured)
tail -f /var/log/supervisor.log

# Search for specific request
grep "request_id=abc123" /var/log/supervisor.log
```

---

## 3. Metrics

### Prometheus Metrics

If prometheus is enabled, metrics available at `/metrics`:

```
# Supervisor metrics
supervisor_requests_total{status, intent, risk_level}
supervisor_requests_duration_ms{intent}
supervisor_approval_required_total
supervisor_approval_approved_total
supervisor_approval_rejected_total

# Agent metrics
supervisor_agent_duration_ms{agent_name}
supervisor_agent_errors_total{agent_name}

# LLM metrics
llm_requests_total{model, provider}
llm_requests_duration_ms{model, provider}
llm_errors_total{model, provider}

# Cache metrics
supervisor_cache_hits_total
supervisor_cache_misses_total
```

### Grafana Dashboards

Import from `grafana/dashboards/` (if provided):
- Supervisor Overview
- Request Latency
- Approval Queue
- LLM Performance

---

## 4. Database

### Connection Pool

Monitor via `/health/detailed`:
```json
{
  "database": {
    "pool_size": 10,
    "overflow": 5,
    "checked_in": 8,
    "checked_out": 2
  }
}
```

If seeing pool exhaustion:
- Increase `DB_POOL_SIZE` and `DB_MAX_OVERFLOW`
- Check for long-running queries
- Add connection timeout

### Common Queries

```sql
-- View recent requests
SELECT id, request_id, created_at, status 
FROM messages 
ORDER BY created_at DESC LIMIT 20;

-- View approval queue
SELECT id, user_id, confidence, status, created_at 
FROM approvals 
WHERE status = 'pending' 
ORDER BY created_at ASC;

-- View audit logs
SELECT request_id, decision, risk_level, agents_used, processing_time_ms
FROM audit_logs 
ORDER BY created_at DESC LIMIT 50;

-- View memory items
SELECT scope, scope_id, content, confidence_score, ttl_at 
FROM memory_items 
WHERE ttl_at > NOW() 
ORDER BY created_at DESC;
```

### Backup

```bash
# Manual backup
pg_dump -h localhost -U postgres supervisor_db > backup_$(date +%Y%m%d).sql

# Auto-backup (cron)
0 2 * * * pg_dump -h localhost -U postgres supervisor_db | gzip > /backup/supervisor_$(date +\%Y\%m\%d).sql.gz
```

### Restore

```bash
# Restore from backup
psql -h localhost -U postgres supervisor_db < backup_20240411.sql
```

---

## 5. Redis

### Keys & TTL

```bash
# List keys
redis-cli KEYS "supervisor:*"

# Check session TTL
redis-cli TTL "supervisor:session:user123"

# Flush cache (if needed)
redis-cli FLUSHDB
```

### Common Keys

| Pattern | Purpose |
|---------|---------|
| `supervisor:session:*` | User sessions |
| `supervisor:cache:*` | LRU cache |
| `supervisor:approval:*` | Pending approvals |
| `supervisor:rate:*` | Rate limiting |

---

## 6. Troubleshooting

### LLM Connection Issues

```bash
# Test Ollama
curl http://localhost:11434/api/tags

# Test llama.cpp
curl http://localhost:8088/v1/models

# Check model loaded
curl http://localhost:11434/api/generate -d '{"model":"llama3","prompt":"hi"}'
```

**Solutions:**
1. Check LLM provider in `.env`: `LLM_PROVIDER=ollama`
2. Check base URL: `OLLAMA_BASE_URL=http://localhost:11434`
3. Check timeout: `OLLAMA_TIMEOUT=320` (higher for large models)
4. Verify model exists: `ollama list`

### Database Connection Issues

```bash
# Test connection
psql -h localhost -U postgres -d supervisor_db

# Check PostgreSQL logs
docker logs supervisor-postgres

# Common fixes:
# - Ensure DB_HOST, DB_PORT correct
# - Check DB_USER/DB_PASSWORD
# - Verify database exists
```

### High Memory Usage

```bash
# Check RAM
free -h

# Check supervisor process
ps aux | grep supervisor

# Solutions:
# - Use smaller LLM model (Q4 instead of Q8)
# - Reduce DB_POOL_SIZE
# - Disable unnecessary features
# - Add swap space
```

### Slow Response

```bash
# Check processing time from logs
grep "processing_time_ms" /var/log/supervisor.log | tail -20

# If > 8s, common causes:
# - LLM timeout too low (increase OLLAMA_TIMEOUT)
# - Too many subagents (check intent classification)
# - Database slow (check connection pool)
# - Network latency to LLM
```

### Approval Queue Stuck

```bash
# Check pending approvals
curl http://localhost:8000/approvals?status=pending

# Manual approve/reject
curl -X POST http://localhost:8000/approvals/{approval_id}/action \
  -H "Content-Type: application/json" \
  -d '{"action": "approve", "comment": "Approved"}'
```

---

## 7. Performance Tuning

### LLM Optimization

```bash
# Use quantized model (less RAM, faster)
# Recommended: Llama-3.1-8B-Q4_K_M.gguf (~5GB)

# Adjust context length based on use
# Lower = faster, higher = more context
OLLAMA_CONTEXT_LENGTH=4096  # Default: 8192

# Timeout for long queries
OLLAMA_TIMEOUT=320  # 5+ minutes for complex requests
```

### Database Optimization

```bash
# Connection pool tuning
DB_POOL_SIZE=20
DB_MAX_OVERFLOW=40

# Query timeout
DB_POOL_TIMEOUT=30
```

### Cache Settings

```bash
# LRU cache (enabled by default)
ENABLE_LRU_CACHE=true
# Default: 300 items, 600s TTL

# Adjust if needed (in config or env)
```

---

## 8. Maintenance

### Regular Tasks

| Task | Frequency | Command |
|------|-----------|---------|
| Log rotation | Daily | `logrotate` config |
| Database backup | Daily | `pg_dump` cron |
| Health check | Hourly | `curl /health` |
| Clear old sessions | Weekly | Redis TTL cleanup |
| Restart (if needed) | Monthly | Rolling restart |

### Updating

```bash
# Pull latest
git pull origin main

# Update dependencies
pip install -r requirements.txt

# Run migrations
alembic upgrade head

# Restart
sudo systemctl restart supervisor
```

### Rollback

```bash
# If issues after update
git reset --hard v1.2.3  # specific version
pip install -r requirements.txt
alembic downgrade -1
```

---

## 9. Security

### API Keys

Never commit to git:
- `OPENAI_API_KEY`
- `AZURE_OPENAI_KEY`
- `N8N_API_KEY`
- `DB_PASSWORD`

Use environment variables or secrets manager.

### Webhook Security

```bash
# Set webhook secret
WEBHOOK_INPUT_SECRET=your_secure_random_string

# Verify in requests (if using n8n)
# n8n webhook header: x-webhook-secret
```

### Rate Limiting

Default: 100 requests per 60 seconds

```bash
# Adjust if needed
RATE_LIMIT_REQUESTS=100
RATE_LIMIT_WINDOW=60
```

---

## 10. Emergency Contacts

| Issue | Contact |
|-------|---------|
| LLM down | DevOps |
| Database issue | DBA |
| Production incident | On-call |

---

## Quick Reference

```bash
# Health check
curl http://localhost:8000/health

# View logs
tail -f /var/log/supervisor.log

# Check processes
ps aux | grep supervisor

# Restart service
sudo systemctl restart supervisor

# Database backup
pg_dump -h localhost -U postgres supervisor_db > backup.sql

# Test LLM
curl -X POST http://localhost:11434/api/generate \
  -d '{"model":"llama3","prompt":"test"}'
```

---

## Related Docs

- `DEPLOY_GUIDE.md` - Initial setup
- `SRS.md` - System requirements
- `FLOW.md` - Architecture
- `CONFIGURATION_GUIDE.md` - Configuration details