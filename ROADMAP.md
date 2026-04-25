# Supervisor API Roadmap

## v1.3.0 - Image Processing Enhancements

### Planned
- [ ] **Vision OCR Integration** — Extract text from images using vision-capable LLM (llama3.1-vision, etc.)
  - Upload image attachments to vision model
  - Extract error messages / text from screenshots
  - Use extracted text for KB search and matching
  - Track OCR confidence in metadata
- [ ] Image case auto-routing based on OCR results

---

## v1.2.x - Completed

### v1.2.3 (Current)
- [x] Split customer_reply vs internal_note in ChatResponse
- [x] Separate image model config (OLLAMA_IMAGE_MODEL)
- [x] Image case workflow improvements

### v1.2.2
- [x] Confidence-based routing to Power Automate
- [x] KB clarification flow

### v1.2.1
- [x] Supervisor API initial release

---

## Ideas

- Multi-language support (Vietnamese + English)
- Real-time conversation analytics
- Advanced pattern learning with feedback loops