from .approvals import router as approvals_router
from .chat import router as chat_router
from .feedback import router as feedback_router
from .health import router as health_router
from .knowledge import router as knowledge_router
from .n8n import router as n8n_router

__all__ = ["approvals_router", "chat_router", "feedback_router", "health_router", "knowledge_router", "n8n_router"]
