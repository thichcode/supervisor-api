# Power Automate Image Support for Teams (P2)

## Goal
Update Power Automate flow to extract and send images from Teams messages to supervisor-api.

## Changes to Existing Flow

### 1. Update Trigger — Capture Image Attachments

In Power Automate, modify the trigger to also capture images:

**Trigger: When a new channel message is added**

Add dynamic content extraction:

```json
{
  "message_content": @{triggerBody()?['body']?['content']},
  "attachments": @{triggerBody()?['attachments']},
  "image_urls": @{body('Get_attachment_content')?['imageUrls']}
}
```

### 2. Add Action: Get Attachment Content

**Action:** Get attachment content (built-in Teams connector)

- **Attachment ID:** @{items('Apply_to_each')?['id']}
- **Content type:** Image

### 3. Convert Image to Base64 or Upload to Temp Storage

**Option A — Direct Base64 (simpler for testing)**
```json
{
  "image_base64": @{base64(body('Get_attachment_content'))},
  "content_type": "image/png"
}
```

**Option B — Upload to Azure Blob / ngrok temp (recommended for production)**
- Upload image to temporary public URL
- Attach URL in webhook payload

### 4. Modify Webhook Payload to Include Images

When calling supervisor-api webhook, add:

```json
{
  "type": "user_message_with_image",
  "user_id": @{triggerBody()?['from']?['id']},
  "display_name": @{triggerBody()?['from']?['name']},
  "message_text": @{triggerBody()?['body']?['content']},
  "images": [
    {
      "url": "@{outputs('Upload_image_to_temp')?['image_url']}",
      "alt": "User uploaded image"
    }
  ],
  "channel_id": @{triggerBody()?['channelId']}
}
```

### 5. Handle Retry on Image Processing Failure

Add a **Scope** action with retry policy:

- Retry count: 3
- Retry interval: 10 seconds
- On failure: Send fallback message without image

## Supervisor API Changes Required (Backend)

### 1. New Endpoint: `/chat/with-image`

```python
@router.post("/chat/with-image")
async def chat_with_image(request: ChatWithImageRequest):
    # Extract text from image using vision model (P3)
    ocr_text = await vision_processor.extract_text(request.images)
    
    # Combine user message + OCR text
    augmented_message = f"{request.message_text}\n[Image contains: {ocr_text}]"
    
    # Existing KB search and response flow
    return await process_chat_message(...)
```

### 2. Vision Processor Stub (for P2 placeholder)

```python
# Placeholder until P3 is implemented
async def extract_text_from_image(image_url: str) -> str:
    # P3 will implement llama3.1-vision
    return "[Image detected — OCR pending P3]"
```

## Testing Checklist for P2

- [ ] Power Automate flow can extract image attachment ID
- [ ] Flow can download image content
- [ ] Flow sends webhook with `images` array
- [ ] Supervisor API accepts `/chat/with-image` request
- [ ] Placeholder response does not break existing approval flow
- [ ] Log image receipt for P3 debugging

## Rollback Plan

If image flow causes issues:

1. Disable the image branch in Power Automate (keep existing text-only flow)
2. Set env var `ENABLE_IMAGE_SUPPORT=false` in supervisor-api
3. No downtime for current users

## Next Step After P2

P3: Vision OCR Integration (llama3.1-vision) will replace the placeholder and provide real text extraction from images for KB search.