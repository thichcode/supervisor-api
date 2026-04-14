from .admin import router as admin_router
from .approvals import router as approvals_router
from .chat import router as chat_router
from .feedback import router as feedback_router
from .health import router as health_router
from .knowledge import router as knowledge_router
from .knowledge_files import router as knowledge_files_router
from .monitoring import router as monitoring_router
from .n8n import router as n8n_router

__all__ = ["admin_router", "approvals_router", "chat_router", "feedback_router", "health_router", "knowledge_router", "knowledge_files_router", "monitoring_router", "n8n_router"]
