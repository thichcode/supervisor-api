"""
Audit Logger - Structured audit logging for compliance
Records all significant actions, decisions, and system events
"""

import json
import time
import uuid
from typing import Optional, List, Dict, Any, Union
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
import structlog

logger = structlog.get_logger()


class AuditEventType(str, Enum):
    """Types of audit events"""
    # Authentication
    USER_LOGIN = "user.login"
    USER_LOGOUT = "user.logout"
    LOGIN_FAILED = "user.login_failed"
    
    # Data access
    DATA_READ = "data.read"
    DATA_WRITE = "data.write"
    DATA_DELETE = "data.delete"
    DATA_EXPORT = "data.export"
    
    # Actions
    ACTION_REQUESTED = "action.requested"
    ACTION_APPROVED = "action.approved"
    ACTION_REJECTED = "action.rejected"
    ACTION_EXECUTED = "action.executed"
    ACTION_FAILED = "action.failed"
    
    # Configuration
    CONFIG_CHANGED = "config.changed"
    SETTING_UPDATED = "setting.updated"
    
    # System
    SYSTEM_START = "system.start"
    SYSTEM_STOP = "system.stop"
    SYSTEM_ERROR = "system.error"
    SYSTEM_WARNING = "system.warning"
    
    # AI/ML
    AI_QUERY = "ai.query"
    AI_RESPONSE = "ai.response"
    AI_DECISION = "ai.decision"
    AI_CONFIDENCE_LOW = "ai.confidence_low"
    
    # Compliance
    COMPLIANCE_CHECK = "compliance.check"
    COMPLIANCE_VIOLATION = "compliance.violation"
    PRIVACY_ACCESS = "privacy.access"
    PII_ACCESSED = "pii.accessed"
    
    # Business
    REPORT_GENERATED = "report.generated"
    NOTIFICATION_SENT = "notification.sent"
    SCHEDULE_TRIGGERED = "schedule.triggered"


class RiskLevel(str, Enum):
    """Risk levels for events"""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class AuditEvent:
    """Audit event record"""
    event_id: str
    event_type: str
    timestamp: datetime
    
    # Who
    user_id: Optional[str] = None
    user_name: Optional[str] = None
    user_role: Optional[str] = None
    session_id: Optional[str] = None
    
    # What
    action: Optional[str] = None
    resource_type: Optional[str] = None
    resource_id: Optional[str] = None
    
    # Details
    details: Dict[str, Any] = field(default_factory=dict)
    previous_value: Optional[Any] = None
    new_value: Optional[Any] = None
    
    # Context
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    request_id: Optional[str] = None
    correlation_id: Optional[str] = None
    
    # Assessment
    risk_level: RiskLevel = RiskLevel.LOW
    outcome: str = "success"  # success, failure, pending
    
    # Compliance
    compliance_tags: List[str] = field(default_factory=list)
    retention_days: int = 365
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        data = asdict(self)
        data['timestamp'] = self.timestamp.isoformat()
        data['risk_level'] = self.risk_level.value if isinstance(self.risk_level, RiskLevel) else self.risk_level
        return data
    
    def to_json(self) -> str:
        """Convert to JSON string"""
        return json.dumps(self.to_dict(), default=str, ensure_ascii=False)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'AuditEvent':
        """Create from dictionary"""
        if isinstance(data.get('timestamp'), str):
            data['timestamp'] = datetime.fromisoformat(data['timestamp'].replace('Z', '+00:00'))
        if isinstance(data.get('risk_level'), str):
            data['risk_level'] = RiskLevel(data['risk_level'])
        return cls(**data)


class AuditLogger:
    """
    Structured audit logger
    Supports multiple outputs: file, database, SIEM, etc.
    """
    
    def __init__(
        self,
        service_name: str = "supervisor",
        log_file: Optional[str] = None,
        enable_console: bool = True,
        enable_structlog: bool = True,
        min_risk_level: RiskLevel = RiskLevel.LOW,
    ):
        self.service_name = service_name
        self.log_file = log_file
        self.enable_console = enable_console
        self.enable_structlog = enable_structlog
        self.min_risk_level = min_risk_level
        
        self._events: List[AuditEvent] = []
        self._max_events = 10000  # In-memory buffer
        
        # Get structlog logger
        self._log = structlog.get_logger("audit")
    
    def log(
        self,
        event_type: Union[str, AuditEventType],
        user_id: Optional[str] = None,
        user_name: Optional[str] = None,
        action: Optional[str] = None,
        resource_type: Optional[str] = None,
        resource_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        risk_level: Union[str, RiskLevel] = RiskLevel.LOW,
        outcome: str = "success",
        **kwargs
    ) -> AuditEvent:
        """
        Log an audit event
        
        Args:
            event_type: Type of event
            user_id: User ID
            user_name: User display name
            action: Action performed
            resource_type: Type of resource affected
            resource_id: ID of resource
            details: Additional details
            risk_level: Risk level
            outcome: success/failure/pending
            **kwargs: Additional fields
        """
        # Create event
        event = AuditEvent(
            event_id=str(uuid.uuid4())[:16],
            event_type=event_type.value if isinstance(event_type, AuditEventType) else event_type,
            timestamp=datetime.now(timezone.utc),
            user_id=user_id,
            user_name=user_name,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            details=details or {},
            risk_level=risk_level.value if isinstance(risk_level, RiskLevel) else RiskLevel(risk_level),
            outcome=outcome,
            **kwargs
        )
        
        # Store in memory
        self._events.append(event)
        if len(self._events) > self._max_events:
            self._events = self._events[-self._max_events:]
        
        # Log to structlog
        if self.enable_structlog:
            self._log_event(event)
        
        # Write to file
        if self.log_file:
            self._write_to_file(event)
        
        return event
    
    def _log_event(self, event: AuditEvent):
        """Log event to structlog"""
        log_data = {
            "event_id": event.event_id,
            "event_type": event.event_type,
            "user_id": event.user_id,
            "action": event.action,
            "resource_type": event.resource_type,
            "resource_id": event.resource_id,
            "risk_level": event.risk_level.value,
            "outcome": event.outcome,
            "service": self.service_name,
        }
        
        # Add non-None details
        for key, value in event.details.items():
            if value is not None:
                log_data[f"detail_{key}"] = value
        
        if event.risk_level.value in ("high", "critical"):
            self._log.warning("audit_event", **log_data)
        else:
            self._log.info("audit_event", **log_data)
    
    def _write_to_file(self, event: AuditEvent):
        """Write event to log file"""
        try:
            with open(self.log_file, 'a') as f:
                f.write(event.to_json() + "\n")
        except Exception as e:
            logger.error("Failed to write audit log", file=self.log_file, error=str(e))
    
    # Convenience methods
    
    def log_user_action(
        self,
        user_id: str,
        user_name: str,
        action: str,
        details: Optional[Dict[str, Any]] = None,
        **kwargs
    ) -> AuditEvent:
        """Log user action"""
        return self.log(
            event_type=AuditEventType.DATA_WRITE,
            user_id=user_id,
            user_name=user_name,
            action=action,
            details=details,
            **kwargs
        )
    
    def log_ai_query(
        self,
        user_id: str,
        query: str,
        response: str,
        confidence: float,
        agents_used: List[str],
        **kwargs
    ) -> AuditEvent:
        """Log AI query and response"""
        risk = RiskLevel.HIGH if confidence < 0.8 else RiskLevel.MEDIUM if confidence < 0.9 else RiskLevel.LOW
        
        return self.log(
            event_type=AuditEventType.AI_QUERY,
            user_id=user_id,
            action="ai_query",
            details={
                "query": query[:500],  # Truncate
                "response_preview": response[:200],
                "confidence": confidence,
                "agents_used": agents_used,
            },
            risk_level=risk,
            **kwargs
        )
    
    def log_action_request(
        self,
        user_id: str,
        user_name: str,
        action_name: str,
        parameters: Dict[str, Any],
        risk_level: RiskLevel,
        request_id: str,
        **kwargs
    ) -> AuditEvent:
        """Log action request with approval"""
        event_type = AuditEventType.ACTION_REQUESTED
        
        return self.log(
            event_type=event_type,
            user_id=user_id,
            user_name=user_name,
            action=action_name,
            resource_type="action_request",
            resource_id=request_id,
            details={
                "parameters": parameters,
                "risk_level": risk_level.value,
            },
            risk_level=risk_level,
            outcome="pending",
            **kwargs
        )
    
    def log_approval(
        self,
        request_id: str,
        action: str,
        approved: bool,
        approver_id: Optional[str] = None,
        approver_name: Optional[str] = None,
        comment: Optional[str] = None,
        **kwargs
    ) -> AuditEvent:
        """Log approval/rejection"""
        event_type = AuditEventType.ACTION_APPROVED if approved else AuditEventType.ACTION_REJECTED
        
        return self.log(
            event_type=event_type,
            user_id=approver_id,
            user_name=approver_name,
            action=action,
            resource_type="approval",
            resource_id=request_id,
            details={
                "approved": approved,
                "comment": comment,
            },
            outcome="success" if approved else "rejected",
            **kwargs
        )
    
    def log_compliance(
        self,
        check_type: str,
        result: str,
        details: Dict[str, Any],
        passed: bool,
        **kwargs
    ) -> AuditEvent:
        """Log compliance check"""
        event_type = AuditEventType.COMPLIANCE_CHECK if passed else AuditEventType.COMPLIANCE_VIOLATION
        risk = RiskLevel.MEDIUM if passed else RiskLevel.HIGH
        
        return self.log(
            event_type=event_type,
            action=check_type,
            details={
                "result": result,
                **details
            },
            risk_level=risk,
            compliance_tags=[check_type],
            outcome="success" if passed else "violation",
            **kwargs
        )
    
    def log_pii_access(
        self,
        user_id: str,
        accessed_by: str,
        resource_type: str,
        resource_id: str,
        pii_types: List[str],
        **kwargs
    ) -> AuditEvent:
        """Log PII access (GDPR/privacy compliance)"""
        return self.log(
            event_type=AuditEventType.PII_ACCESSED,
            user_id=user_id,
            action="pii_access",
            resource_type=resource_type,
            resource_id=resource_id,
            details={
                "accessed_by": accessed_by,
                "pii_types": pii_types,
            },
            risk_level=RiskLevel.HIGH,
            compliance_tags=["gdpr", "pii"],
            **kwargs
        )
    
    # Query methods
    
    def get_events(
        self,
        event_type: Optional[str] = None,
        user_id: Optional[str] = None,
        resource_type: Optional[str] = None,
        risk_level: Optional[RiskLevel] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        limit: int = 100,
    ) -> List[AuditEvent]:
        """Query audit events"""
        events = self._events
        
        if event_type:
            events = [e for e in events if e.event_type == event_type]
        
        if user_id:
            events = [e for e in events if e.user_id == user_id]
        
        if resource_type:
            events = [e for e in events if e.resource_type == resource_type]
        
        if risk_level:
            events = [e for e in events if e.risk_level == risk_level]
        
        if start_time:
            events = [e for e in events if e.timestamp >= start_time]
        
        if end_time:
            events = [e for e in events if e.timestamp <= end_time]
        
        return events[-limit:]
    
    def get_user_activity(
        self,
        user_id: str,
        limit: int = 50
    ) -> List[AuditEvent]:
        """Get activity for a specific user"""
        return self.get_events(user_id=user_id, limit=limit)
    
    def get_failed_events(
        self,
        limit: int = 50
    ) -> List[AuditEvent]:
        """Get failed events"""
        return [e for e in self._events if e.outcome == "failure"][-limit:]
    
    def get_high_risk_events(
        self,
        limit: int = 50
    ) -> List[AuditEvent]:
        """Get high and critical risk events"""
        return [
            e for e in self._events
            if e.risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL)
        ][-limit:]
    
    def get_compliance_events(
        self,
        tag: str,
        limit: int = 50
    ) -> List[AuditEvent]:
        """Get events by compliance tag"""
        return [
            e for e in self._events
            if tag in e.compliance_tags
        ][-limit:]
    
    # Statistics
    
    def get_stats(
        self,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Get audit statistics"""
        events = self._events
        
        if start_time:
            events = [e for e in events if e.timestamp >= start_time]
        if end_time:
            events = [e for e in events if e.timestamp <= end_time]
        
        return {
            "total_events": len(events),
            "by_type": self._count_by(events, "event_type"),
            "by_user": self._count_by(events, "user_id"),
            "by_risk_level": {
                r.value: sum(1 for e in events if e.risk_level == r)
                for r in RiskLevel
            },
            "by_outcome": self._count_by(events, "outcome"),
            "compliance_tags": list(set(
                tag for e in events for tag in e.compliance_tags
            )),
        }
    
    def _count_by(self, events: List[AuditEvent], attr: str) -> Dict[str, int]:
        """Count events by attribute"""
        counts: Dict[str, int] = {}
        for e in events:
            value = getattr(e, attr, None)
            if value:
                counts[value] = counts.get(value, 0) + 1
        return counts
    
    def export_json(
        self,
        file_path: str,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
    ) -> int:
        """Export events to JSON file"""
        events = self.get_events(start_time=start_time, end_time=end_time, limit=999999)
        
        with open(file_path, 'w') as f:
            f.write("[\n")
            for i, event in enumerate(events):
                f.write(event.to_json())
                if i < len(events) - 1:
                    f.write(",\n")
            f.write("\n]")
        
        return len(events)
    
    def clear(self):
        """Clear in-memory events"""
        self._events.clear()


# Global audit logger
_audit_logger: Optional[AuditLogger] = None


def get_audit_logger(**kwargs) -> AuditLogger:
    """Get or create global audit logger"""
    global _audit_logger
    if _audit_logger is None:
        _audit_logger = AuditLogger(**kwargs)
    return _audit_logger


# Decorator for automatic audit logging
def audited(
    event_type: str,
    action: Optional[str] = None,
    risk_level: RiskLevel = RiskLevel.LOW,
):
    """Decorator for automatic audit logging"""
    def decorator(func):
        async def async_wrapper(*args, **kwargs):
            audit = get_audit_logger()
            try:
                result = await func(*args, **kwargs)
                audit.log(
                    event_type=event_type,
                    action=action or func.__name__,
                    details={"args": str(args)[:200], "kwargs": str(kwargs)[:200]},
                    risk_level=risk_level,
                    outcome="success",
                )
                return result
            except Exception as e:
                audit.log(
                    event_type=event_type,
                    action=action or func.__name__,
                    details={"error": str(e)},
                    risk_level=risk_level,
                    outcome="failure",
                )
                raise
        
        def sync_wrapper(*args, **kwargs):
            audit = get_audit_logger()
            try:
                result = func(*args, **kwargs)
                audit.log(
                    event_type=event_type,
                    action=action or func.__name__,
                    details={"args": str(args)[:200], "kwargs": str(kwargs)[:200]},
                    risk_level=risk_level,
                    outcome="success",
                )
                return result
            except Exception as e:
                audit.log(
                    event_type=event_type,
                    action=action or func.__name__,
                    details={"error": str(e)},
                    risk_level=risk_level,
                    outcome="failure",
                )
                raise
        
        import asyncio
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper
    
    return decorator
