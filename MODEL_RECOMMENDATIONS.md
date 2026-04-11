# AI Model Recommendations

## Recommended Models by Use Case

| Use Case | Recommended Model | Description | Confidence |
|----------|-------------------|-------------|-------------|
| **FAQ** | llama3 | Quick factual answers | High |
| **Policy** | llama3 | Policy interpretation | High |
| **Support Case** | llama3 | Technical support | High |
| **Analysis** | llama3 | Data analysis | High |
| **Executive** | llama3 | High-priority requests | High |

## Override Model per Intent

Set in environment:
```bash
# Use specific model for all requests
LLM_MODEL=llama3

# Or use model per intent (in code)
LLM_MODEL_FAQ=llama3
LLM_MODEL_POLICY=llama3
LLM_MODEL_SUPPORT=llama3
```

## Model Selection Logic

In `Supervisor.process()`:
```python
# Select model based on intent
model_map = {
    IntentType.FAQ: settings.recommended_models.get("faq"),
    IntentType.POLICY: settings.recommended_models.get("policy"),
    IntentType.SUPPORT_CASE: settings.recommended_models.get("support_case"),
    IntentType.ANALYSIS: settings.recommended_models.get("analysis"),
    IntentType.EXECUTIVE_REQUEST: settings.recommended_models.get("executive"),
}
selected_model = model_map.get(intent.intent, settings.recommended_models.get("default"))
```

## Provider Selection

| Provider | Use Case | Config |
|----------|----------|--------|
| **Ollama** (Recommended) | Default for all Vietnamese | `LLM_PROVIDER=ollama` |
| **llama.cpp** | Production with GPU | `LLM_PROVIDER=ollama` + custom URL |
| **OpenAI** | Fallback / Cloud | `LLM_PROVIDER=openai` |
| **Azure** | Enterprise | `LLM_PROVIDER=azure` |

## Performance Notes

- Ollama (llama3): ~500ms for typical responses
- OpenAI GPT-4: ~800ms, higher cost
- Azure OpenAI: ~600ms, enterprise SLA

For Vietnamese outsourcing companies, **Ollama with llama3** provides the best balance of speed, cost, and language support.