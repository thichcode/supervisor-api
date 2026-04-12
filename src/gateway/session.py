"""
Gateway Session Store - Persist conversation sessions
"""

import json
from pathlib import Path
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field
from datetime import datetime
import structlog

logger = structlog.get_logger()


@dataclass
class Message:
    """A message in the conversation"""
    role: str  # user, assistant, system
    content: str
    timestamp: datetime = field(default_factory=datetime.utcnow)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Session:
    """A conversation session"""
    session_id: str
    platform: str
    user_id: str
    messages: List[Message] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    metadata: Dict[str, Any] = field(default_factory=dict)


class SessionStore:
    """
    Store and retrieve conversation sessions
    """
    
    def __init__(self, storage_path: Optional[Path] = None):
        if storage_path is None:
            self.storage_path = Path.home() / ".supervisor" / "gateway" / "sessions"
        else:
            self.storage_path = storage_path
        
        self.storage_path.mkdir(parents=True, exist_ok=True)
        self._sessions: Dict[str, Session] = {}
    
    def _get_session_file(self, session_id: str) -> Path:
        """Get session file path"""
        return self.storage_path / f"{session_id}.json"
    
    def create_session(
        self,
        session_id: str,
        platform: str,
        user_id: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Session:
        """Create a new session"""
        session = Session(
            session_id=session_id,
            platform=platform,
            user_id=user_id,
            metadata=metadata or {}
        )
        
        self._sessions[session_id] = session
        self._save_session(session)
        
        logger.info("Session created", session_id=session_id, platform=platform)
        return session
    
    def get_session(self, session_id: str) -> Optional[Session]:
        """Get a session by ID"""
        if session_id in self._sessions:
            return self._sessions[session_id]
        
        # Try to load from disk
        session_file = self._get_session_file(session_id)
        if session_file.exists():
            try:
                data = json.loads(session_file.read_text())
                session = Session(
                    session_id=data["session_id"],
                    platform=data["platform"],
                    user_id=data["user_id"],
                    messages=[Message(**m) for m in data.get("messages", [])],
                    metadata=data.get("metadata", {})
                )
                self._sessions[session_id] = session
                return session
            except Exception as e:
                logger.warning("Failed to load session", session_id=session_id, error=str(e))
        
        return None
    
    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Message:
        """Add a message to a session"""
        session = self.get_session(session_id)
        
        if not session:
            raise ValueError(f"Session not found: {session_id}")
        
        message = Message(
            role=role,
            content=content,
            metadata=metadata or {}
        )
        
        session.messages.append(message)
        session.updated_at = datetime.utcnow()
        
        self._save_session(session)
        
        return message
    
    def get_history(self, session_id: str, limit: int = 50) -> List[Message]:
        """Get conversation history"""
        session = self.get_session(session_id)
        
        if not session:
            return []
        
        return session.messages[-limit:]
    
    def clear_history(self, session_id: str):
        """Clear session history"""
        session = self.get_session(session_id)
        
        if session:
            session.messages.clear()
            session.updated_at = datetime.utcnow()
            self._save_session(session)
    
    def _save_session(self, session: Session):
        """Save session to disk"""
        session_file = self._get_session_file(session.session_id)
        
        data = {
            "session_id": session.session_id,
            "platform": session.platform,
            "user_id": session.user_id,
            "messages": [
                {
                    "role": m.role,
                    "content": m.content,
                    "timestamp": m.timestamp.isoformat(),
                    "metadata": m.metadata
                }
                for m in session.messages
            ],
            "created_at": session.created_at.isoformat(),
            "updated_at": session.updated_at.isoformat(),
            "metadata": session.metadata
        }
        
        session_file.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        self._sessions[session.session_id] = session


# Global instance
_session_store: Optional[SessionStore] = None


def get_session_store() -> SessionStore:
    """Get global session store"""
    global _session_store
    if _session_store is None:
        _session_store = SessionStore()
    return _session_store


__all__ = ["SessionStore", "Session", "Message", "get_session_store"]