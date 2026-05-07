# Hướng Dẫn Setup Power Automate - Teams Approval Flow

## Mục Lục
1. [Tổng Quan Kiến Trúc](#tổng-quan-kiến-trúc)
2. [Bước 1: Tạo Power Automate Flow Mới](#bước-1-tạo-power-automate-flow-mới)
3. [Bước 2: Setup Trigger](#bước-2-setup-trigger)
4. [Bước 3: Parse JSON từ Supervisor](#bước-3-parse-json-từ-supervisor)
5. [Bước 4: Post Adaptive Card lên Teams](#bước-4-post-adaptive-card-lên-teams)
6. [Bước 5: Wait for Approval](#bước-5-wait-for-approval)
7. [Bước 6: Call Supervisor API để Approve/Reject](#bước-6-call-supervisor-api-để-approvereject)
8. [Bước 7: Test Flow](#bước-7-test-flow)
9. [Troubleshooting](#troubleshooting)

---

## Tổng Quan Kiến Trúc

```
┌──────────────────────────────────────────────────────────────────────────┐
│                                                                          │
│   USER                    POWER AUTOMATE              SUPERVISOR         │
│   ────                    ───────────────              ───────────       │
│                                                                          │
│   ┌──────┐                      ┌────────────┐                           │
│   │Teams │ ─────────────────────▶│  Trigger   │                           │
│   └──────┘                      └──────┬─────┘                           │
│                                        │                                  │
│                                        ▼                                  │
│                               ┌────────────────┐                         │
│                               │ Parse JSON     │                         │
│                               │ (approval_req) │                         │
│                               └──────┬─────────┘                         │
│                                        │                                  │
│                                        ▼                                  │
│                               ┌────────────────┐    ┌─────────────────┐ │
│                               │ Adaptive Card  │───▶│   ADMIN TEAMS   │ │
│                               │ (Approve/Reject)│    │   📩 Message   │ │
│                               └──────┬─────────┘    └─────────────────┘ │
│                                        │                                  │
│                                        ▼                                  │
│                               ┌────────────────┐                         │
│                               │ Wait for       │                         │
│                               │ Approval       │◀──────────────────────│ │
│                               └──────┬─────────┘      Admin clicks      │
│                                        │              Approve/Reject    │
│                                        ▼                                  │
│                               ┌────────────────┐                         │
│                               │ HTTP POST      │ ──────────────────────▶│
│                               │ /approvals/act │   Supervisor executes  │
│                               └────────────────┘    & sends to Teams     │
│                                                                          │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## Bước 1: Tạo Power Automate Flow Mới

### 1.1. Đăng nhập Power Automate
1. Mở trình duyệt → Truy cập: **https://make.powerautomate.com**
2. Đăng nhập với tài khoản Microsoft của bạn

### 1.2. Tạo Automated Flow
1. Click **Create** (bên trái menu)
2. Chọn **Automated cloud flow**
3. Đặt tên: `Supervisor AI Approval Flow`
4. Click **Skip**

### 1.3. Hoặc Tạo Instant Flow (for testing)
1. Click **Create** → **Instant cloud flow**
2. Đặt tên: `Test Supervisor Approval`
3. Chọn trigger: **Manually trigger a flow**
4. Click **Create**

---

## Bước 2: Setup Trigger (Updated for Image Support)

### Option A: Manual Trigger (Testing)
```
Trigger: Manually trigger a flow
```

Thêm input:
- **Text input**: approval_id
- **Text input**: action (approve/reject)
- **Text input**: reviewed_by

### Option B: When Teams Message Received (Production)

**Trigger: When a new channel message is added (with image support)**

```
1. Trigger: When a channel message contains specific words
   - Channel: [Chọn Teams channel của bạn]
   - Trigger Condition: 
     - Message contains 'approve' OR 'reject' OR has attachments
   - Advanced: Include attachments = Yes
```

**Hoặc dùng Request trigger cho webhook:**

```
Trigger: When a HTTP request is received
- Method: POST
- Body (JSON Schema):
{
  "type": "object",
  "properties": {
    "type": {"type": "string"},
    "approval_id": {"type": "string"},
    "user_id": {"type": "string"},
    "display_name": {"type": "string"},
    "original_message": {"type": "string"},
    "ai_response": {"type": "string"},
    "confidence": {"type": "number"},
    "message": {"type": "string"}
  }
}
```

---

## Bước 3: Parse JSON từ Supervisor

### Thêm Action: Parse JSON

**quan trọng: Content phải là `@triggerBody()` - đây là cách lấy toàn bộ HTTP body**

```
Action: Parse JSON
- Content: @triggerBody()     ← quan trọng nhất!
- Schema: (dùng nút "Generate from sample" paste payload mẫu bên dưới)
```

### Sample Payload để paste vào Power Automate

Click **"Generate from sample"** trong Parse JSON action và paste:

```json
{
  "type": "approval_request",
  "approval_id": "test-123",
  "request_id": "req-456",
  "user_id": "thuong",
  "display_name": "Thuong",
  "original_message": "xin chào",
  "ai_response": "Xin chào! Tôi là Supervisor",
  "confidence": 38.0,
  "threshold": 90.0,
  "message": "⚠️ Cần duyệt phản hồi cho Thuong",
  "timestamp": "2026-04-12T12:00:00"
}
```

**GIẢI THÍCH:**
- `@triggerBody()` - lấy toàn bộ JSON body từ HTTP request Supervisor gửi
- Parse JSON sẽ tự tạo schema khớp với payload

```json
{
  "type": "object",
  "properties": {
    "type": {
      "type": "string",
      "description": "Message type (approval_request)"
    },
    "approval_id": {
      "type": "string",
      "description": "Unique approval ID"
    },
    "request_id": {
      "type": "string",
      "description": "Original request ID"
    },
    "user_id": {
      "type": "string",
      "description": "User ID"
    },
    "display_name": {
      "type": "string",
      "description": "User display name"
    },
    "original_message": {
      "type": "string",
      "description": "Original user message"
    },
    "ai_response": {
      "type": "string",
      "description": "AI generated response"
    },
    "confidence": {
      "type": "number",
      "description": "Confidence percentage"
    },
    "threshold": {
      "type": "number",
      "description": "Approval threshold"
    },
    "message": {
      "type": "string",
      "description": "Formatted message for Teams"
    },
    "timestamp": {
      "type": "string",
      "description": "Request timestamp"
    }
  }
}
```

---

## Bước 4: Post Adaptive Card lên Teams

### Thêm Action: Post Adaptive Card

```
Action: Post adaptive card and wait for a response
- Team: [Chọn Teams team của bạn]
- Channel: [Chọn channel để post]
- Message:
```

Paste JSON sau vào phần **Adaptive Card**:

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
            "type": "Container",
            "style": "attention",
            "items": [
              {
                "type": "TextBlock",
                "text": "⚠️ AI Response Approval Required",
                "weight": "Bolder",
                "size": "Large",
                "horizontalAlignment": "Center",
                "color": "Warning"
              }
            ]
          },
          {
            "type": "FactSet",
            "facts": [
              {
                "title": "👤 User",
                "value": "@{body('Parse_JSON')?['display_name']}"
              },
              {
                "title": "📊 Confidence",
                "value": "@{body('Parse_JSON')?['confidence']}%"
              },
              {
                "title": "📝 Original Message",
                "value": "@{body('Parse_JSON')?['original_message']}"
              }
            ]
          },
          {
            "type": "TextBlock",
            "text": "AI Response Preview:",
            "weight": "Bolder",
            "spacing": "Medium"
          },
          {
            "type": "TextBlock",
            "text": "@{body('Parse_JSON')?['ai_response']}",
            "wrap": true,
            "maxLines": 8,
            "spacing": "Small"
          }
        ],
        "actions": [
          {
            "type": "Action.Execute",
            "title": "✅ Approve",
            "style": "positive",
            "data": {
              "approval_id": "@{body('Parse_JSON')?['approval_id']}",
              "action": "approve"
            }
          },
          {
            "type": "Action.Execute",
            "title": "❌ Reject",
            "style": "destructive",
            "data": {
              "approval_id": "@{body('Parse_JSON')?['approval_id']}",
              "action": "reject"
            }
          }
        ],
        "msteams": {
          "width": "Full"
        }
      }
    }
  ]
}
```

---

## Bước 5: Wait for Approval

### Thêm Action: Wait for approval response

Sau khi post Adaptive Card, flow sẽ tự động chờ response từ user.

**Output từ action này:**
- `selectedOption`: "Approve" hoặc "Reject"
- `approveId`: ID của approval request
- `responses`: Comments từ approver

### Thêm Condition

```
Condition: selectedOption equals 'Approve'
```

#### If yes (Approve):
```
1. HTTP POST to Supervisor
   - URI: http://[server-ip]:8000/approvals/@{body('Parse_JSON')?['approval_id']}/action
   - Method: POST
   - Headers:
     Content-Type: application/json
   - Body:
     {
       "action": "approve",
       "reviewed_by": "Teams Admin",
       "comment": "Approved via Teams"
     }

2. Post message to Teams
   - Post as: Flow bot
   - Message: "✅ Đã approve response cho @{body('Parse_JSON')?['display_name']}"
```

#### If no (Reject):
```
1. HTTP POST to Supervisor
   - URI: http://[server-ip]:8000/approvals/@{body('Parse_JSON')?['approval_id']}/action
   - Method: POST
   - Headers:
     Content-Type: application/json
   - Body:
     {
       "action": "reject",
       "reviewed_by": "Teams Admin",
       "comment": "Rejected via Teams"
     }

2. Post message to Teams
   - Post as: Flow bot
   - Message: "❌ Đã reject response cho @{body('Parse_JSON')?['display_name']}"
```

---

## Bước 6: Call Supervisor API để Approve/Reject

### Hoàn chỉnh HTTP POST Action

```
HTTP Action: POST to Supervisor Approval Endpoint

URI: http://[YOUR_SERVER_IP]:8000/approvals/@{body('Parse_JSON')?['approval_id']}/action

Method: POST

Headers:
  Content-Type: application/json

Body:
{
  "action": "@{outputs('Post_adaptive_card_and_wait')?['selectedOption']}",
  "reviewed_by": "@{triggerOutputs()?['from']?['user']?['displayName']}",
  "comment": "@{outputs('Post_adaptive_card_and_wait')?['comments']}"
}
```

---

## Bước 7: Test Flow

### 7.1. Test Manual Trigger

1. Mở Flow → Click **Test**
2. Chọn **I'll perform the trigger action**
3. Điền thông tin test:
   ```json
   {
     "type": "approval_request",
     "approval_id": "9e310ac6-9b83-4de3-8882-ff3de98d41c3",
     "user_id": "test-user",
     "display_name": "Test User",
     "original_message": "Hello, I need help",
     "ai_response": "Hello! I'm here to help. How can I assist you today?",
     "confidence": 38.0,
     "threshold": 90.0,
     "message": "⚠️ Cần duyệt phản hồi cho Test User"
   }
   ```
4. Click **Run flow**

### 7.2. Test từ Supervisor

```bash
# Gửi message để tạo approval
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "I need help with my computer",
    "user_id": "thuong",
    "display_name": "Thuong"
  }' --max-time 60
```

### 7.3. Approve qua API

```bash
# Get approval_id từ response, sau đó approve
curl -X POST http://localhost:8000/approvals/[approval_id]/action \
  -H "Content-Type: application/json" \
  -d '{
    "action": "approve",
    "reviewed_by": "thuong",
    "comment": "Test approval"
  }'
```

---

## Troubleshooting

### Issue 1: Adaptive Card không hiện buttons

**Nguyên nhân:** Teams connector chưa được cấu hình đúng

**Giải pháp:**
1. Kiểm tra Flow bot đã được thêm vào Teams channel chưa
2. Vào Teams → Channel → Add member → Tìm "Power Automate" bot
3. Hoặc dùng "Post a message using Flow bot" thay vì Adaptive Card

### Issue 2: Flow không nhận được webhook

**Nguyên nhân:** Server không accessible từ internet

**Giải pháp:**
1. Dùng ngrok để test local:
   ```bash
   ngrok http 8000
   ```
2. Copy ngrok URL vào .env thay thế localhost
3. Sau khi test xong, deploy server lên cloud (Azure, AWS, etc.)

### Issue 3: Approval ID không tìm thấy

**Nguyên nhân:** Approval đã expire hoặc sai ID

**Giải pháp:**
```bash
# Check pending approvals
curl http://localhost:8000/approvals | python -m json.tool

# Nếu không có pending, tạo approval mới
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "test", "user_id": "test", "display_name": "Test"}'
```

### Issue 4: HTTP 401 Unauthorized

**Nguyên nhân:** Sai webhook secret

**Giải pháp:**
1. Kiểm tra `WEBHOOK_INPUT_SECRET` trong .env
2. Đảm bảo Power Automate gửi header `X-Webhook-Secret` đúng

---

## Cấu Hình Server (Production)

### Firewall
```
Inbound: Allow port 8000 from Power Automate IPs
- 13.64.0.0/11 (Microsoft Azure)
- 52.0.0.0/8 (Microsoft Azure)
```

### HTTPS (Production)
```
Khuyến nghị: Sử dụng HTTPS cho production
- Có thể dùng nginx reverse proxy với SSL
- Hoặc Azure App Service với built-in SSL
```

### Environment Variables
```bash
# /tmp/supervisor-api/.env
DB_HOST=localhost
DB_PORT=5432
DB_NAME=supervisor_db
DB_USER=postgres
DB_PASSWORD=postgres

REDIS_HOST=localhost
REDIS_PORT=6379

LLM_PROVIDER=ollama
LLM_MODEL=llama3
OLLAMA_BASE_URL=http://localhost:8088
OLLAMA_TIMEOUT=320

# Power Automate webhook URL
POWER_AUTOMATE_WEBHOOK_URL=https://[your-tenant].logic.azure.com:443/workflows/...

# Webhook security
WEBHOOK_INPUT_SECRET=your_secure_secret_here
```

---

## Flow Diagram Hoàn Chỉnh

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        POWER AUTOMATE FLOW                                  │
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │ TRIGGER: When a HTTP request is received                            │   │
│  │          (POST from Supervisor)                                       │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                    │                                        │
│                                    ▼                                        │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │ ACTION: Parse JSON                                                   │   │
│  │         Content: triggerBody()                                      │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                    │                                        │
│                                    ▼                                        │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │ CONDITION: type == "approval_request"                                │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                          │                                                 │
│            ┌─────────────┴─────────────┐                                   │
│            │                           │                                    │
│           Yes                          No                                   │
│            │                           │                                    │
│            ▼                           ▼                                    │
│  ┌──────────────────┐        ┌────────────────────┐                        │
│  │ Post Adaptive    │        │ Terminate (ignore) │                        │
│  │ Card & Wait      │        └────────────────────┘                        │
│  └────────┬─────────┘                                                      │
│           │                                                                 │
│           ▼                                                                 │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │ CONDITION: selectedOption == "Approve"                               │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                          │                                                 │
│            ┌─────────────┴─────────────┐                                   │
│            │                           │                                    │
│           Yes                          No                                   │
│            │                           │                                    │
│            ▼                           ▼                                    │
│  ┌──────────────────┐        ┌──────────────────┐                           │
│  │ HTTP POST        │        │ HTTP POST        │                           │
│  │ /approvals/act   │        │ /approvals/act   │                           │
│  │ action=approve   │        │ action=reject    │                           │
│  └────────┬─────────┘        └────────┬─────────┘                           │
│           │                           │                                    │
│           └─────────────┬─────────────┘                                    │
│                         ▼                                                   │
│              ┌──────────────────────┐                                       │
│              │ Post message to Teams│                                       │
│              │ (confirmation)       │                                       │
│              └──────────────────────┘                                       │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Links Hữu Ích

- Power Automate Documentation: https://docs.microsoft.com/en-us/power-automate/
- Adaptive Cards: https://adaptivecards.io/
- Teams Connector: https://docs.microsoft.com/en-us/connectors/teams/
- HTTP Connector: https://docs.microsoft.com/en-us/connectors/http/

---

## Hỗ Trợ

Nếu gặp vấn đề:
1. Kiểm tra Flow run history trong Power Automate
2. Xem logs: `tail -f /tmp/supervisor.log`
3. Check pending approvals: `curl http://localhost:8000/approvals`
