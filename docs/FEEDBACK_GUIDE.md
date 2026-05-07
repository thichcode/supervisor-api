# User Feedback & Learning Guide

This guide explains how the Supervisor API collects feedback from end users (Teams, Telegram, etc.) and uses it to improve responses.

## Overview

The system learns from:
- **Explicit feedback** – Thumbs up/down, comments, ticket resolution
- **Implicit feedback** – Repeated questions, approval overrides, fallback triggers

All feedback is processed by the `FeedbackReplayWorker` (runs every 60 seconds) and updates the knowledge base.

---

## 1. Feedback from Microsoft Teams Users

### Option A: Power Automate Flow (Recommended)

Create a Power Automate flow that captures user reactions and sends them to the Supervisor webhook.

**Trigger:** "When a Teams message reaction is added" (for 👍/👎)

**Payload format to send to Supervisor:**

```json
{
  "request_id": "original-request-id-if-known",
  "feedback_type": "positive|negative|neutral",
  "user_id": "teams-user-id",
  "user_name": "User Display Name",
  "thread_id": "teams-conversation-id",
  "message_id": "original-message-id",
  "comment": "Optional user comment",
  "ticket_id": "ITC12345",
  "original_question": "What the user asked",
  "original_answer": "What the bot replied",
  "reaction": "thumbsup|thumbsdown|smile|sad"
}
```

**Power Automate HTTP action:**
- Method: `POST`
- URL: `https://your-supervisor.com/webhook/feedback`
- Headers: `Content-Type: application/json`
- Body: Use the payload above

### Option B: Adaptive Card with Feedback Buttons

Include feedback buttons in the bot's response adaptive card:

```json
{
  "type": "AdaptiveCard",
  "body": [
    { "type": "TextBlock", "text": "{{answer}}" }
  ],
  "actions": [
    {
      "type": "Action.Submit",
      "title": "👍 Helpful",
      "data": { "feedback": "positive", "request_id": "{{request_id}}" }
    },
    {
      "type": "Action.Submit",
      "title": "👎 Not Helpful",
      "data": { "feedback": "negative", "request_id": "{{request_id}}" }
    },
    {
      "type": "Action.Submit",
      "title": "📝 Need ticket",
      "data": { "feedback": "ticket_request", "request_id": "{{request_id}}" }
    }
  ],
  "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
  "version": "1.4"
}
```

---

## 2. Feedback from Telegram Users

### Inline Keyboard Buttons

Add feedback buttons to Telegram responses:

```python
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

keyboard = [
    [InlineKeyboardButton("👍 Helpful", callback_data=f"feedback_positive_{request_id}")],
    [InlineKeyboardButton("👎 Not Helpful", callback_data=f"feedback_negative_{request_id}")]
]
reply_markup = InlineKeyboardMarkup(keyboard)
```

### Callback Handler

The Supervisor already has `/telegram-callback` endpoint (in `src/api/app.py`) that handles callback queries.

---

## 3. Feedback API Endpoint

The Supervisor provides a `/webhook/feedback` endpoint (add if not exists):

```python
@app.post("/webhook/feedback")
async def receive_feedback(feedback: FeedbackPayload):
    """Receive feedback from Teams/Telegram/other channels."""
    await interaction_service.record_feedback(
        request_id=feedback.request_id,
        feedback_type=feedback.feedback_type,
        user_id=feedback.user_id,
        comment=feedback.comment,
        ticket_id=feedback.ticket_id
    )
    
    # Trigger learning worker
    await feedback_worker.replay_once()
    
    return {"status": "recorded"}
```

**FeedbackPayload model:**

```python
class FeedbackPayload(BaseModel):
    request_id: Optional[str] = None
    feedback_type: Literal["positive", "negative", "neutral"]
    user_id: str
    user_name: Optional[str] = None
    thread_id: Optional[str] = None
    message_id: Optional[str] = None
    comment: Optional[str] = None
    ticket_id: Optional[str] = None
    original_question: Optional[str] = None
    original_answer: Optional[str] = None
    reaction: Optional[str] = None
```

---

## 4. Feedback Processing Flow

```
User reaction (👍/👎)
       │
       ▼
Power Automate / Telegram bot
       │
       ▼
Supervisor /webhook/feedback
       │
       ▼
InteractionService.record_feedback()
       │
       ├── Store in interaction_logs (feedback_type column)
       ├── If negative: increment kb_miss_count
       └── If positive: increment kb_hit_count
       │
       ▼
FeedbackReplayWorker (every 60s)
       │
       ├── Query interactions with feedback
       ├── For negative feedback:
       │    ├── Extract missing knowledge pattern
       │    ├── Create KB draft candidate
       │    └── Send to Telegram approval channel
       └── For positive feedback:
            ├── Reinforce existing KB entries
            └── Adjust confidence scores
```

---

## 5. Configuration for Feedback

Add to `config/config.yaml`:

```yaml
feedback:
  enabled: true
  replay_interval_seconds: 60
  min_negative_feedback_for_draft: 3
  auto_create_ticket_on_negative: false
  telegram_approval_channel: "@your_approval_channel"
```

Environment variables:

```bash
FEEDBACK_ENABLED=true
FEEDBACK_REPLAY_INTERVAL=60
FEEDBACK_TELEGRAM_APPROVAL_CHANNEL=@kb_approvals
```

---

## 6. Monitoring Feedback

### Prometheus Metrics

- `supervisor_feedback_total{type="positive|negative"}` – Count of feedback received
- `supervisor_kb_drafts_created_total` – KB candidates from negative feedback
- `supervisor_feedback_learning_processed_total` – Feedback items processed

### Query Examples

```sql
-- Most frequent negative feedback patterns
SELECT kb_miss_pattern, COUNT(*) 
FROM interaction_logs 
WHERE feedback_type = 'negative' 
GROUP BY kb_miss_pattern 
ORDER BY COUNT(*) DESC 
LIMIT 10;

-- KB improvement over time
SELECT DATE(created_at), 
       COUNT(CASE WHEN feedback_type='positive' THEN 1 END) as positive,
       COUNT(CASE WHEN feedback_type='negative' THEN 1 END) as negative
FROM interaction_logs
WHERE feedback_type IS NOT NULL
GROUP BY DATE(created_at);
```

---

## 7. Troubleshooting

| Issue | Solution |
|-------|----------|
| Feedback not recorded | Check `/webhook/feedback` endpoint is accessible, verify webhook secret |
| Negative feedback not creating drafts | Ensure `min_negative_feedback_for_draft = 3` and Telegram approval channel configured |
| Feedback worker not running | Check `feedback_worker_task` is created in lifespan, verify logs for errors |
| Duplicate feedback | Implement idempotency key or check existing feedback before inserting |

---

## 8. Example: Power Automate Flow for Teams Feedback

1. **Trigger:** When a reaction is added to a message (outside of Microsoft Teams, use HTTP trigger or polling)
2. **Get message details** (using Graph API)
3. **Condition:** If reaction is 👍 or 👎
4. **HTTP action:** POST to Supervisor `/webhook/feedback` with payload
5. **Response:** Log result

**Sample Power Automate expression for payload:**
```javascript
{
  "feedback_type": "positive", // or negative
  "user_id": "{{triggerOutputs().headers['x-ms-user-id']}}",
  "thread_id": "{{triggerOutputs().body['conversation']['id']}}",
  "message_id": "{{triggerOutputs().body['id']}}"
}
```

---

## Related Documentation

- [Power Automate Setup](./POWER_AUTOMATE_SETUP.md)
- [Approval Workflow](./approval-teams-flow.md)
- [API Reference](./api.md)