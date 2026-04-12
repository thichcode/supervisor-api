# Power Automate Flow - Teams Approval

## Flow Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                                                                     │
│  1. User gửi message Teams                                          │
│         ↓                                                           │
│  2. Power Automate Trigger (When Teams message)                     │
│         ↓                                                           │
│  3. Call Supervisor /webhook/n8n                                    │
│         ↓                                                           │
│  4. Supervisor xử lý → confidence < 90%                            │
│         ↓                                                           │
│  5. Supervisor gọi Power Automate (approval_notification)           │
│         ↓                                                           │
│  6. Power Automate post Adaptive Card Teams → Admin                 │
│         ↓                                                           │
│  7. Admin click Approve/Reject                                      │
│         ↓                                                           │
│  8. Power Automate call /approvals/{id}/action                      │
│         ↓                                                           │
│  9. Supervisor execute → gửi response cho user qua Teams            │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

## Step-by-Step Setup

### Flow 1: Main Processing (Teams → Supervisor → Teams)

**Trigger:** When a new Teams message is received (via "Post a message as the Flow bot to a channel")

#### Actions:

```
1. When a message is posted (trigger)
   - Team: [your team]
   - Channel: [approval channel]

2. Initialize variables:
   - approval_id: ""
   - needs_approval: false

3. HTTP POST to Supervisor:
   - URL: http://[your-server]:8000/webhook/n8n
   - Method: POST
   - Headers: Content-Type: application/json
   - Body:
     {
       "request_id": "@{triggerOutputs()?['body']?['id']}",
       "timestamp": "@{triggerOutputs()?['body']?['createdDateTime']}",
       "source": "teams",
       "user": {
         "id": "@{triggerOutputs()?['body']?['from']?['user']?['id']}",
         "display_name": "@{triggerOutputs()?['body']?['from']?['user']?['displayName']}"
       },
       "conversation": {
         "thread_id": "@{triggerOutputs()?['body']?['conversation']?['id']}",
         "message_id": "@{triggerOutputs()?['body']?['id']}"
       },
       "message": {
         "text": "@{triggerOutputs()?['body']?['body']?['content']}"
       }
     }

4. Condition: body.approval_required == true
   - Yes → Post Adaptive Card to Teams (approval request)
   - No → Post message directly to Teams
```

### Flow 2: Approval Request Handler (Supervisor → Teams)

**Trigger:** When a Teams message is posted (using "When a message is posted" trigger)

#### Actions:

```
1. Parse JSON from Power Automate webhook
   - Content: @{triggerBody()}

2. Condition: type == "approval_request"
   - Yes → Continue
   - No → Terminate

3. Post Adaptive Card to Teams:
   - Message: Adaptive Card JSON (see below)
   - Post as: Flow bot
   - In: [approval channel]

4. Wait for approval (using "Wait for an approval" action)
   - Approval type: Approve/Reject
   - Title: "AI Response Approval Required"
   - Assigned to: [admin email]
   - Details: AI response preview

5. Condition: outcome == "Approve"
   - Yes → HTTP POST to Supervisor:
     - URL: http://[your-server]:8000/approvals/@{variables('approval_id')}/action
     - Body: {"action": "approve", "reviewed_by": "admin", "comment": "Approved via Teams"}
   - No → HTTP POST to Supervisor:
     - URL: http://[your-server]:8000/approvals/@{variables('approval_id')}/action
     - Body: {"action": "reject", "reviewed_by": "admin", "comment": "Rejected via Teams"}
```

### Adaptive Card JSON (Approval Request):

```json
{
  "type": "message",
  "attachments": [
    {
      "contentType": "application/vnd.microsoft.card.adaptive",
      "content": {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.4",
        "body": [
          {
            "type": "TextBlock",
            "text": "⚠️ AI Response Approval Required",
            "weight": "Bolder",
            "size": "Large",
            "color": "Warning"
          },
          {
            "type": "FactSet",
            "facts": [
              {
                "title": "User",
                "value": "@{body('Parse_JSON')?['display_name']}"
              },
              {
                "title": "Confidence",
                "value": "@{body('Parse_JSON')?['confidence']}%"
              },
              {
                "title": "Original Message",
                "value": "@{body('Parse_JSON')?['original_message']}"
              }
            ]
          },
          {
            "type": "TextBlock",
            "text": "**AI Response Preview:**",
            "weight": "Bolder"
          },
          {
            "type": "TextBlock",
            "text": "@{body('Parse_JSON')?['ai_response']}",
            "wrap": true,
            "maxLines": 5
          }
        ],
        "actions": [
          {
            "type": "ActionSet",
            "actions": [
              {
                "type": "Action.Execute",
                "title": "✅ Approve",
                "verb": "approve",
                "data": {
                  "approval_id": "@{body('Parse_JSON')?['approval_id']}",
                  "action": "approve"
                }
              },
              {
                "type": "Action.Execute",
                "title": "❌ Reject",
                "verb": "reject",
                "data": {
                  "approval_id": "@{body('Parse_JSON')?['approval_id']}",
                  "action": "reject"
                }
              }
            ]
          }
        ]
      }
    }
  ]
}
```

### Flow 3: Handle Adaptive Card Response

**Trigger:** When a Teams message is posted (with specific keywords)

#### Actions:

```
1. When a message is posted (trigger)
   - Team: [your team]
   - Channel: [approval channel]

2. Parse message content to extract action and approval_id

3. Condition: message contains "approve" or "reject"

4. HTTP POST to Supervisor:
   - URL: http://[your-server]:8000/approvals/{approval_id}/action
   - Method: POST
   - Headers: Content-Type: application/json
   - Body:
     {
       "action": "approve",
       "reviewed_by": "@{triggerOutputs()?['body']?['from']?['user']?['displayName']}",
       "comment": "Approved via Teams"
     }

5. Post confirmation to Teams:
   - Message: "✅ Approved by @{triggerOutputs()?['body']?['from']?['user']?['displayName']}"
```

## Testing the Flow

### Test 1: Send test message
```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "xin chao",
    "user_id": "test-user",
    "display_name": "Test User"
  }'
```

### Test 2: Approve via API
```bash
# Get approval ID from response
curl -X POST http://localhost:8000/approvals/{approval_id}/action \
  -H "Content-Type: application/json" \
  -d '{
    "action": "approve",
    "reviewed_by": "thuong",
    "comment": "OK"
  }'
```

### Test 3: Check pending approvals
```bash
curl http://localhost:8000/approvals | python -m json.tool
```

## Troubleshooting

### Issue: Power Automate not receiving webhook
- Check firewall allows inbound to port 8000
- Verify webhook URL is accessible from internet
- Use ngrok for local testing: `ngrok http 8000`

### Issue: Adaptive Card not showing buttons
- Ensure Teams connector is properly configured
- Check Flow bot has permission to post in channel

### Issue: Approval not executing
- Check approval_id is correct
- Verify Power Automate webhook URL in .env
- Check logs: `tail -f /tmp/supervisor.log`

## Environment Variables

In `/tmp/supervisor-api/.env`:
```
POWER_AUTOMATE_WEBHOOK_URL=https://[your-tenant].logic.azure.com:443/workflows/...
WEBHOOK_INPUT_SECRET=your_secret_here
```
