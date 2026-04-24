"""
Tool Registry - Standardized Tool Handling

Provides a centralized registry for managing agent tools:
- Tool registration with metadata
- Standardized execution
- Error handling and retries
- Tool categorization
"""

import asyncio
import json
import logging
import os
import shlex
import subprocess
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from src.config import get_settings
from src.core.approval import approval_service

settings = get_settings()
logger = logging.getLogger("harness.tool_registry")


class ToolCategory(Enum):
    """Categories for organizing tools"""
    TERMINAL = "terminal"
    FILE = "file"
    WEB = "web"
    CODE = "code"
    DATA = "data"
    KNOWLEDGE = "knowledge"
    SYSTEM = "system"
    CUSTOM = "custom"


@dataclass
class ToolDefinition:
    """Definition of a registered tool"""
    name: str
    description: str
    category: ToolCategory = ToolCategory.CUSTOM
    parameters: Dict[str, Any] = field(default_factory=dict)
    handler: Optional[Callable] = None
    requires_approval: bool = False
    dangerous: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_schema(self) -> Dict[str, Any]:
        """Convert to OpenAI tool schema format"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters or {"type": "object", "properties": {}},
            }
        }


@dataclass
class ToolExecutionResult:
    """Result of a tool execution"""
    tool_name: str
    success: bool
    result: Any
    error: Optional[str] = None
    duration_ms: float = 0
    timestamp: datetime = field(default_factory=datetime.now)


class ToolRegistry:
    """
    Centralized registry for managing agent tools
    
    Features:
    - Register tools with metadata
    - Execute tools with standardized handling
    - Error handling and retries
    - Tool categorization
    """
    
    def __init__(self):
        self._tools: Dict[str, ToolDefinition] = {}
        self._categories: Dict[ToolCategory, List[str]] = {cat: [] for cat in ToolCategory}
        self._initialized = False
        self._execution_history: List[ToolExecutionResult] = []
    
    def register(
        self,
        name: str,
        description: str,
        handler: Callable,
        category: ToolCategory = ToolCategory.CUSTOM,
        parameters: Optional[Dict[str, Any]] = None,
        requires_approval: bool = False,
        dangerous: bool = False,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Register a new tool"""
        tool = ToolDefinition(
            name=name,
            description=description,
            category=category,
            parameters=parameters or {},
            handler=handler,
            requires_approval=requires_approval,
            dangerous=dangerous,
            metadata=metadata or {},
        )
        
        self._tools[name] = tool
        self._categories[category].append(name)
        
        logger.info(f"Registered tool: {name} (category: {category.value})")
    
    def unregister(self, name: str) -> bool:
        """Unregister a tool"""
        if name in self._tools:
            tool = self._tools[name]
            self._categories[tool.category].remove(name)
            del self._tools[name]
            logger.info(f"Unregistered tool: {name}")
            return True
        return False
    
    def get(self, name: str) -> Optional[ToolDefinition]:
        """Get a tool by name"""
        return self._tools.get(name)
    
    def list_by_category(self, category: ToolCategory) -> List[ToolDefinition]:
        """List all tools in a category"""
        return [self._tools[name] for name in self._categories.get(category, [])]
    
    def list_all(self) -> List[ToolDefinition]:
        """List all registered tools"""
        return list(self._tools.values())
    
    def get_schemas(self) -> List[Dict[str, Any]]:
        """Get all tool schemas for LLM"""
        return [tool.to_schema() for tool in self._tools.values()]
    
    async def initialize(self) -> None:
        """Initialize with built-in tools"""
        if self._initialized:
            return
        
        # Register built-in tools
        self._register_builtin_tools()
        
        self._initialized = True
        logger.info(f"ToolRegistry initialized with {len(self._tools)} tools")
    
    def _register_builtin_tools(self) -> None:
        """Register built-in tools"""
        
        # Terminal tools
        self.register(
            name="terminal",
            description="Execute shell commands in terminal",
            handler=self._terminal_handler,
            category=ToolCategory.TERMINAL,
            dangerous=True,
            parameters={
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command to execute"},
                    "timeout": {"type": "integer", "description": "Timeout in seconds"},
                    "workdir": {"type": "string", "description": "Working directory"},
                },
                "required": ["command"],
            },
        )
        
        # File tools
        self.register(
            name="read_file",
            description="Read content from a file",
            handler=self._read_file_handler,
            category=ToolCategory.FILE,
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path to read"},
                    "offset": {"type": "integer", "description": "Line offset (1-indexed)"},
                    "limit": {"type": "integer", "description": "Max lines to read"},
                },
                "required": ["path"],
            },
        )
        
        self.register(
            name="write_file",
            description="Write content to a file",
            handler=self._write_file_handler,
            category=ToolCategory.FILE,
            dangerous=True,
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path to write"},
                    "content": {"type": "string", "description": "Content to write"},
                },
                "required": ["path", "content"],
            },
        )
        
        self.register(
            name="search_files",
            description="Search for patterns in files",
            handler=self._search_files_handler,
            category=ToolCategory.FILE,
            parameters={
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Regex pattern"},
                    "path": {"type": "string", "description": "Directory to search"},
                    "file_glob": {"type": "string", "description": "File glob pattern"},
                    "limit": {"type": "integer", "description": "Max results"},
                },
                "required": ["pattern"],
            },
        )
        
        # Web tools
        self.register(
            name="web_search",
            description="Search the web for information",
            handler=self._web_search_handler,
            category=ToolCategory.WEB,
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "num_results": {"type": "integer", "description": "Number of results"},
                },
                "required": ["query"],
            },
        )
        
        # Code execution
        self.register(
            name="execute_code",
            description="Execute Python code",
            handler=self._execute_code_handler,
            category=ToolCategory.CODE,
            dangerous=True,
            parameters={
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "Python code to execute"},
                    "language": {"type": "string", "description": "Programming language"},
                },
                "required": ["code"],
            },
        )
        
        # Knowledge tools
        self.register(
            name="search_knowledge",
            description="Search the knowledge base",
            handler=self._search_knowledge_handler,
            category=ToolCategory.KNOWLEDGE,
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "category": {"type": "string", "description": "KB category filter"},
                },
                "required": ["query"],
            },
        )
        
        # System tools
        self.register(
            name="delegate_task",
            description="Delegate a task to a sub-agent",
            handler=self._delegate_task_handler,
            category=ToolCategory.SYSTEM,
            parameters={
                "type": "object",
                "properties": {
                    "goal": {"type": "string", "description": "Task goal for sub-agent"},
                    "context": {"type": "string", "description": "Additional context"},
                    "toolsets": {"type": "array", "description": "Toolsets to enable"},
                },
                "required": ["goal"],
            },
        )
    
    async def execute(
        self,
        name: str,
        arguments: Dict[str, Any],
        max_retries: int = 3,
        approved: bool = False,
        approval_context: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """Execute a tool with standardized handling.

        Dangerous tools are routed through Telegram approval unless `approved=True`.
        """
        start_time = datetime.now()

        tool = self._tools.get(name)
        if not tool:
            result = ToolExecutionResult(
                tool_name=name,
                success=False,
                result=None,
                error=f"Tool not found: {name}",
                duration_ms=0,
            )
            self._execution_history.append(result)
            raise ValueError(f"Tool not found: {name}")

        if (tool.dangerous or tool.requires_approval) and not approved:
            approval_context = approval_context or {}
            request_id = approval_context.get("request_id") or f"tool-{uuid.uuid4().hex[:8]}"
            user_id = approval_context.get("user_id") or "system"
            display_name = approval_context.get("display_name") or "Supervisor Agent"
            original_message = approval_context.get("original_message") or (
                f"{name}({json.dumps(arguments, ensure_ascii=False, default=str)})"
            )
            ai_response = approval_context.get("ai_response") or (
                f"Tool '{name}' is pending human approval before execution."
            )
            confidence = float(approval_context.get("confidence", 0.5))
            metadata = {
                "tool_name": name,
                "tool_arguments": arguments,
                "tool_category": tool.category.value,
                "dangerous": tool.dangerous,
                "requires_approval": tool.requires_approval or tool.dangerous,
                "requested_via": approval_context.get("requested_via", "harness"),
                "thread_id": approval_context.get("thread_id", ""),
                "platform": approval_context.get("platform", ""),
                "chat_type": approval_context.get("chat_type", ""),
                "chat_scope": approval_context.get("chat_scope", ""),
                "group_chat": approval_context.get("group_chat"),
                **(approval_context.get("metadata") or {}),
            }

            approval = await approval_service.create_approval(
                request_id=request_id,
                user_id=user_id,
                display_name=display_name,
                original_message=original_message,
                ai_response=ai_response,
                confidence=confidence,
                action_type=f"tool:{name}",
                metadata=metadata,
            )
            duration_ms = (datetime.now() - start_time).total_seconds() * 1000
            result = ToolExecutionResult(
                tool_name=name,
                success=False,
                result={
                    "success": False,
                    "pending_approval": True,
                    "approval_id": approval.id,
                    "request_id": request_id,
                    "tool_name": name,
                    "message": f"Tool '{name}' requires Telegram approval before execution.",
                },
                error="approval_required",
                duration_ms=duration_ms,
            )
            self._execution_history.append(result)
            logger.info(
                f"Tool approval requested: tool={name} approval_id={approval.id} request_id={request_id} dangerous={tool.dangerous}"
            )
            return result.result

        # Execute with retries
        last_error = None
        for attempt in range(max_retries):
            try:
                if asyncio.iscoroutinefunction(tool.handler):
                    result_data = await tool.handler(**arguments)
                else:
                    result_data = tool.handler(**arguments)

                duration_ms = (datetime.now() - start_time).total_seconds() * 1000

                result = ToolExecutionResult(
                    tool_name=name,
                    success=True,
                    result=result_data,
                    duration_ms=duration_ms,
                )
                self._execution_history.append(result)

                logger.debug(f"Tool executed: {name} ({duration_ms:.2f}ms)")
                return result_data

            except Exception as e:
                last_error = e
                logger.warning(f"Tool execution failed (attempt {attempt + 1}/{max_retries}): {name} - {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(0.5 * (attempt + 1))  # Exponential backoff

        # All retries failed
        duration_ms = (datetime.now() - start_time).total_seconds() * 1000
        result = ToolExecutionResult(
            tool_name=name,
            success=False,
            result=None,
            error=str(last_error),
            duration_ms=duration_ms,
        )
        self._execution_history.append(result)

        raise last_error or Exception(f"Tool execution failed: {name}")
    
    # Built-in tool handlers
    def _terminal_handler(self, command: str, timeout: int = 30, workdir: str = None) -> Dict:
        """Handle terminal execution"""
        from src.cli_tools import terminal
        result = terminal(command, timeout=timeout, workdir=workdir)
        return result
    
    def _read_file_handler(self, path: str, offset: int = 1, limit: int = 500) -> Dict:
        """Handle file reading"""
        from src.cli_tools import read_file
        return read_file(path, offset=offset, limit=limit)
    
    def _write_file_handler(self, path: str, content: str) -> Dict:
        """Handle file writing"""
        from src.cli_tools import write_file
        return write_file(path, content)
    
    def _search_files_handler(self, pattern: str, path: str = ".", file_glob: str = None, limit: int = 50) -> Dict:
        """Handle file search"""
        from src.cli_tools import search_files
        return search_files(pattern, path=path, file_glob=file_glob, limit=limit)
    
    def _web_search_handler(self, query: str, num_results: int = 5) -> Dict:
        """Handle web search"""
        from src.cli_tools import web_search
        return web_search(query, num_results=num_results)
    
    def _execute_code_handler(self, code: str, language: str = "python") -> Dict:
        """Handle code execution"""
        from src.cli_tools import execute_code
        return execute_code(code)
    
    def _search_knowledge_handler(self, query: str, category: str = None) -> Dict:
        """Handle knowledge base search"""
        from src.knowledge.manager import KnowledgeManager
        km = KnowledgeManager()
        return km.search(query, category=category)
    
    def _delegate_task_handler(self, goal: str, context: str = None, toolsets: List[str] = None) -> Dict:
        """Handle task delegation"""
        from src.subagents import SubagentPool
        pool = SubagentPool()
        return pool.run_single(goal, context, toolsets)
    
    def get_execution_history(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Get execution history"""
        return [vars(r) for r in self._execution_history[-limit:]]
    
    def get_stats(self) -> Dict[str, Any]:
        """Get registry statistics"""
        total_executions = len(self._execution_history)
        successful = sum(1 for r in self._execution_history if r.success)
        failed = total_executions - successful
        
        return {
            "total_tools": len(self._tools),
            "total_executions": total_executions,
            "successful": successful,
            "failed": failed,
            "success_rate": successful / total_executions if total_executions > 0 else 0,
            "categories": {cat.value: len(tools) for cat, tools in self._categories.items()},
        }


# Global registry instance
_registry: Optional[ToolRegistry] = None


def get_tool_registry() -> ToolRegistry:
    """Get or create the global tool registry"""
    global _registry
    if _registry is None:
        _registry = ToolRegistry()
    return _registry
