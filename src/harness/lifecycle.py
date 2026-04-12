"""
Lifecycle Hooks - Event-driven callbacks for agent lifecycle

Provides hooks for:
- PRE_BOOT / POST_BOOT
- PRE_EXECUTION / POST_EXECUTION
- PRE_TOOL_CALL / POST_TOOL_CALL
- PRE_CONTEXT_COMPACTION / POST_CONTEXT_COMPACTION
- ON_ERROR
- SHUTDOWN
"""

import asyncio
from typing import Any, Callable, Dict, List, Optional
from enum import Enum
from datetime import datetime
from dataclasses import dataclass, field

import logging
from src.config import get_settings

settings = get_settings()
logger = logging.getLogger("harness.lifecycle")


class HookType(Enum):
    """Types of lifecycle hooks"""
    PRE_BOOT = "pre_boot"
    POST_BOOT = "post_boot"
    PRE_EXECUTION = "pre_execution"
    POST_EXECUTION = "post_execution"
    PRE_TOOL_CALL = "pre_tool_call"
    POST_TOOL_CALL = "post_tool_call"
    PRE_CONTEXT_COMPACTION = "pre_context_compaction"
    POST_CONTEXT_COMPACTION = "post_context_compaction"
    ON_ERROR = "on_error"
    SHUTDOWN = "shutdown"


@dataclass
class HookEvent:
    """Event passed to hooks"""
    hook_type: HookType
    execution_id: str
    timestamp: datetime = field(default_factory=datetime.now)
    data: Dict[str, Any] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    
    def add_error(self, error: str) -> None:
        self.errors.append(error)


@dataclass
class HookRegistration:
    """Registration of a hook callback"""
    hook_type: HookType
    callback: Callable
    priority: int = 0  # Higher priority runs first
    async_mode: bool = True  # Run as async
    
    def __lt__(self, other):
        return self.priority > other.priority  # Higher priority first


class LifecycleHooks:
    """
    Manages lifecycle hooks for the agent harness
    
    Allows registration of callbacks for various lifecycle events:
    - Boot sequence (pre/post)
    - Execution (pre/post)
    - Tool calls (pre/post)
    - Context compaction (pre/post)
    - Error handling
    - Shutdown
    """
    
    def __init__(self):
        self._hooks: Dict[HookType, List[HookRegistration]] = {
            hook_type: [] for hook_type in HookType
        }
        self._event_history: List[HookEvent] = []
    
    def register(
        self,
        hook_type: HookType,
        callback: Callable,
        priority: int = 0,
        async_mode: bool = True,
    ) -> None:
        """Register a hook callback"""
        registration = HookRegistration(
            hook_type=hook_type,
            callback=callback,
            priority=priority,
            async_mode=async_mode,
        )
        
        self._hooks[hook_type].append(registration)
        self._hooks[hook_type].sort()  # Sort by priority
        
        logger.debug(f"Registered hook: {hook_type.value} (priority: {priority})")
    
    def unregister(
        self,
        hook_type: HookType,
        callback: Callable,
    ) -> bool:
        """Unregister a hook callback"""
        hooks = self._hooks[hook_type]
        for i, reg in enumerate(hooks):
            if reg.callback == callback:
                hooks.pop(i)
                logger.debug(f"Unregistered hook: {hook_type.value}")
                return True
        return False
    
    async def run_hooks(
        self,
        hook_type: HookType,
        data: Dict[str, Any],
        execution_id: Optional[str] = None,
    ) -> HookEvent:
        """Run all hooks of a given type"""
        event = HookEvent(
            hook_type=hook_type,
            execution_id=execution_id or "unknown",
            data=data,
        )
        
        registrations = self._hooks.get(hook_type, [])
        
        if not registrations:
            return event
        
        logger.debug(f"Running {len(registrations)} hooks for: {hook_type.value}")
        
        for reg in registrations:
            try:
                if reg.async_mode and asyncio.iscoroutinefunction(reg.callback):
                    await reg.callback(event)
                elif reg.async_mode:
                    await asyncio.coroutine(reg.callback)(event)
                else:
                    reg.callback(event)
            except Exception as e:
                error_msg = f"Hook error ({hook_type.value}): {str(e)}"
                event.add_error(error_msg)
                logger.error(error_msg)
        
        self._event_history.append(event)
        
        # Limit history size
        if len(self._event_history) > 1000:
            self._event_history = self._event_history[-500:]
        
        return event
    
    def get_registered_hooks(self) -> Dict[str, int]:
        """Get count of registered hooks by type"""
        return {
            hook_type.value: len(hooks)
            for hook_type, hooks in self._hooks.items()
        }
    
    def get_event_history(
        self,
        limit: int = 100,
        hook_type: Optional[HookType] = None,
    ) -> List[Dict[str, Any]]:
        """Get event history"""
        events = self._event_history
        if hook_type:
            events = [e for e in events if e.hook_type == hook_type]
        
        return [
            {
                "hook_type": e.hook_type.value,
                "execution_id": e.execution_id,
                "timestamp": e.timestamp.isoformat(),
                "errors": e.errors,
            }
            for e in events[-limit:]
        ]


# Convenience decorators

def pre_boot(priority: int = 0):
    """Decorator for pre-boot hook"""
    def decorator(func: Callable):
        func._hook_type = HookType.PRE_BOOT
        func._hook_priority = priority
        return func
    return decorator


def post_boot(priority: int = 0):
    """Decorator for post-boot hook"""
    def decorator(func: Callable):
        func._hook_type = HookType.POST_BOOT
        func._hook_priority = priority
        return func
    return decorator


def pre_execution(priority: int = 0):
    """Decorator for pre-execution hook"""
    def decorator(func: Callable):
        func._hook_type = HookType.PRE_EXECUTION
        func._hook_priority = priority
        return func
    return decorator


def post_execution(priority: int = 0):
    """Decorator for post-execution hook"""
    def decorator(func: Callable):
        func._hook_type = HookType.POST_EXECUTION
        func._hook_priority = priority
        return func
    return decorator


def on_error(priority: int = 0):
    """Decorator for on-error hook"""
    def decorator(func: Callable):
        func._hook_type = HookType.ON_ERROR
        func._hook_priority = priority
        return func
    return decorator


def on_shutdown(priority: int = 0):
    """Decorator for shutdown hook"""
    def decorator(func: Callable):
        func._hook_type = HookType.SHUTDOWN
        func._hook_priority = priority
        return func
    return decorator


# Built-in hook implementations

async def log_hook(event: HookEvent) -> None:
    """Log all hook events"""
    status = "SUCCESS" if not event.errors else "ERROR"
    logger.info(f"[{status}] {event.hook_type.value} - execution: {event.execution_id}")


async def metrics_hook(event: HookEvent) -> None:
    """Collect metrics from hook events"""
    # TODO: Send to metrics backend
    pass


async def error_notification_hook(event: HookEvent) -> None:
    """Send notification on errors"""
    if event.errors:
        # TODO: Send to Slack/email
        logger.warning(f"Hook errors: {event.errors}")
