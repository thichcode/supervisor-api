# API Reference

Complete API documentation for supervisor-api.

## Base URL

```
http://localhost:8000
```

## Authentication

### Webhook Authentication

```bash
# Header-based auth
x-webhook-secret: <your_secret>
```

### API Key (Future)

```bash
Authorization: Bearer <api_key>
```

---

## Endpoints

### Health

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | Basic liveness probe |
| GET | `/health/ready` | Readiness check (DB + Redis + LLM) |
| GET | `/health/detailed` | Detailed component status |

**Response `/health`:**
```json
{"status": "healthy"}
```

**Response `/health/detailed`:**
```json
{
  "status": "healthy",
  "database": {"status": "connected", "latency_ms": 5},
  "redis": {"status": "connected", "latency_ms": 2},
  "llm": {"status": "available", "model": "llama3"}
}
```

---

### Metrics

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/metrics` | Prometheus metrics |
| GET | `/metrics/dashboard` | Dashboard JSON |
| GET | `/metrics/dashboard/html` | HTML Dashboard |

---

### Chat

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/chat` | Direct user chat |
| POST | `/chat/stream` | Streaming chat |

#### POST `/chat`

**Request:**
```json
{
  "user_id": "user123",
  "display_name": "Nguyen Van A",
  "message": " Xin chào, tôi cần hỗ trợ về VPN",
  "thread_id": "thread_001 (optional)",
  "case_id": "case_001 (optional)",
  "message_type": "text (optional)"
}
```

**Response:**
```json
{
  "request_id": "req_abc123",
  "status": "completed",
  "message": " Xin chào anh/chị...",
  "confidence": 0.92,
  "metadata": {
    "intent": "support_case",
    "risk_level": "low",
    "agents_used": ["context", "policy", "draft", "qa"],
    "processing_time_ms": 3450
  }
}
```

**With Approval Required:**
```json
{
  "request_id": "req_abc123",
  "status": "pending_approval",
  "message": " Xin chào...",
  "confidence": 0.75,
  "metadata": {
    "approval_id": "approval_xyz",
    "approval_required": true,
    "threshold": 0.9
  }
}
```

---

### Webhook

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/webhook/n8n` | Receive from n8n |
| POST | `/output/power-automate` | Send to Power Automate |

#### POST `/webhook/n8n`

**Request:**
```json
{
  "request_id": "req_001",
  "source": "ms_teams",
  "timestamp": "2026-04-12T10:00:00Z",
  "user": {
    "id": "user123",
    "display_name": "Nguyen Van A"
  },
  "conversation": {
    "thread_id": "thread_001",
    "message_id": "msg_001"
  },
  "case": {
    "case_id": "case_001",
    "priority": "medium"
  },
  "message": {
    "text": "Tôi cần hỗ trợ reset password"
  }
}
```

---

### Knowledge Base

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/knowledge/stats` | KB statistics |
| POST | `/knowledge/search` | Search KB |
| POST | `/knowledge/search/enhanced` | LLM-enhanced search |

#### POST `/knowledge/search`

**Request:**
```json
{
  "query": "quy định nghỉ phép",
  "top_k": 5
}
```

**Response:**
```json
{
  "results": [
    {
      "id": "policy_001",
      "title": "Quy định nghỉ phép",
      "content": "...",
      "score": 0.95
    }
  ]
}
```

---

### Approvals

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/approvals` | List approvals |
| GET | `/approvals/{id}` | Get approval |
| POST | `/approvals/{id}/action` | Approve/reject |
| POST | `/approvals/{id}/vote` | User vote |

#### GET `/approvals`

**Query params:** `status=pending|approved|rejected`

**Response:**
```json
{
  "approvals": [
    {
      "id": "approval_001",
      "request_id": "req_001",
      "user_id": "user123",
      "original_message": "...",
      "ai_response": "...",
      "confidence": 0.75,
      "status": "pending",
      "created_at": "2026-04-12T10:00:00Z"
    }
  ]
}
```

#### POST `/approvals/{id}/action`

**Request:**
```json
{
  "action": "approve|reject",
  "comment": "Approved"
}
```

#### POST `/approvals/{id}/vote`

**Request:**
```json
{
  "vote": "agree|change|skip"
}
```

---

### Admin

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/admin/users` | List users |
| POST | `/admin/users` | Create user |
| GET | `/admin/config` | Get config |
| PUT | `/admin/config` | Update config |

*Requires authentication*

---

### Alerts

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/alerts` | List alerts |
| POST | `/alerts` | Create alert |
| PUT | `/alerts/{id}/acknowledge` | Acknowledge |
| DELETE | `/alerts/{id}` | Delete |

---

## Error Responses

| Status | Description |
|--------|-------------|
| 400 | Bad Request - Invalid input |
| 401 | Unauthorized - Invalid auth |
| 403 | Forbidden - No permission |
| 404 | Not Found |
| 422 | Validation Error |
| 429 | Rate Limited |
| 500 | Internal Server Error |
| 503 | Service Unavailable |

**Error format:**
```json
{
  "error": "error_code",
  "detail": "Human readable message"
}
```

---

## Rate Limiting

- Default: 100 requests / 60 seconds
- Headers: `X-RateLimit-Remaining`, `X-RateLimit-Reset`

---

## Websocket (Future)

| Endpoint | Description |
|----------|-------------|
| WS `/ws/chat` | Real-time chat |