"""
Backward-compatibility shim for src/tools/tool_registry.

All functionality moved to src/harness/tool_registry.py.
This file exists to avoid breaking imports in reasoning_loop.py
and any other code that imports from src.tools.tool_registry.

Imported by: src/core/reasoning_loop.py
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

# Re-export everything from the canonical harness registry
from src.harness.tool_registry import (
    ToolRegistry,
    get_tool_registry,
    ToolCategory,
    ToolDefinition,
    ToolExecutionResult,
)

# Backward-compat: lightweight ToolResult wrapper that exposes .output
# reasoning_loop.py accesses .output on tool results
@dataclass
class ToolResult:
    """Result of a single tool execution (backward-compat wrapper)."""
    tool_name: str
    success: bool
    output: str  # exposed as .output for reasoning_loop compatibility
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_execution_result(cls, er: ToolExecutionResult) -> "ToolResult":
        """Convert harness ToolExecutionResult → lightweight ToolResult."""
        output = er.result if isinstance(er.result, str) else str(er.result or "")
        return cls(
            tool_name=er.tool_name,
            success=er.success,
            output=output,
            metadata={"duration_ms": er.duration_ms, **(er.error and {"error": er.error} or {})},
        )


# Backward-compat ToolSpec for build_default_registry()
@dataclass
class ToolSpec:
    """Specification for a registered tool (backward-compat)."""
    name: str
    description: str
    parameters: dict[str, str]
    handler: Callable[..., Awaitable[ToolResult]]


# Wrapped registry that normalizes result to ToolResult for .output access
class ToolRegistryCompat(ToolRegistry):
    """
    Drop-in replacement for the lightweight ToolRegistry.

    Uses the harness ToolRegistry as backend, but:
    - execute() returns ToolResult (with .output) instead of ToolExecutionResult
    - register() accepts either ToolSpec (backward-compat) or individual params
    - Compatible with reasoning_loop.py's expectations
    """

    def register(
        self,
        spec_or_name: "ToolSpec | str",
        description: str = "",
        handler: Callable | None = None,
        category: ToolCategory = ToolCategory.CUSTOM,
        parameters: dict | None = None,
        requires_approval: bool = False,
        dangerous: bool = False,
        metadata: dict | None = None,
    ) -> None:
        """Register: accepts ToolSpec (backward-compat) or individual params."""
        if isinstance(spec_or_name, ToolSpec):
            spec = spec_or_name
            super().register(
                name=spec.name,
                description=spec.description,
                handler=spec.handler,
                category=category,
                parameters={p: {"type": t} for p, t in (spec.parameters or {}).items()},
                requires_approval=requires_approval,
                dangerous=dangerous,
                metadata=metadata,
            )
        else:
            super().register(
                name=spec_or_name,
                description=description,
                handler=handler,
                category=category,
                parameters=parameters or {},
                requires_approval=requires_approval,
                dangerous=dangerous,
                metadata=metadata or {},
            )

    async def execute(self, tool_name: str, arguments: dict[str, Any]) -> ToolResult:
        """Execute: run handler directly, return ToolResult with .output field."""
        tool = self._tools.get(tool_name)
        if not tool:
            return ToolResult(tool_name=tool_name, success=False,
                              output=f"Tool '{tool_name}' not found.")
        try:
            if asyncio.iscoroutinefunction(tool.handler):
                result = await tool.handler(**arguments)
            else:
                result = tool.handler(**arguments)
            # Result may be ToolResult already (from our handlers) or dict
            if isinstance(result, ToolResult):
                return result
            return ToolResult(tool_name=tool_name, success=True,
                              output=str(result)[:4000])
        except Exception as exc:
            return ToolResult(tool_name=tool_name, success=False,
                              output=f"Error: {exc}")


def build_default_registry() -> "ToolRegistryCompat":
    """
    Create a compatible tool registry for reasoning_loop.

    Initializes inline (no async needed) and wraps results so
    reasoning_loop.py can use .output on tool results.
    Matches the tools from src/harness/tool_registry.py but
    registers them via ToolSpec (backward-compat with lightweight registry).
    """
    registry = ToolRegistryCompat()

    def _sanitize_command(command: str) -> tuple[bool, str]:
        """Sanitize shell command to prevent injection.
        
        Returns: (is_safe, error_message)
        """
        if not command:
            return False, "Empty command"
        # Block dangerous patterns
        dangerous = [
            "& ", "; ", "| ", "&&", "||", ">", ">>", "<",
            "\n", "\r", "\x00",  # Control characters
            "sudo ", "su ",  # Privilege escalation
            "curl ", "wget ", "ssh ", "scp ", "rsync ",  # Network
            "rm -rf", "mkfs", "dd if=",  # Destructive
            "python -c", "perl -e", "ruby -e", "php -r",  # Code execution
            "export ", "source /", ". /",  # Env/rc injection
        ]
        cmd_lower = command.lower()
        for pattern in dangerous:
            if pattern in cmd_lower:
                return False, f"Dangerous pattern blocked: {pattern}"
        # Must be alphanumeric + basic punctuation
        import re
        if not re.match(r"^[a-zA-Z0-9\s\-_./:]+$", command):
            return False, "Invalid characters in command"
        return True, ""

    def _sanitize_path(path: str) -> tuple[bool, str]:
        """Sanitize file path to prevent traversal.
        
        Returns: (is_safe, error_message)
        """
        if not path:
            return False, "Empty path"
        # Block absolute paths outside allowed directories
        import os
        abs_path = os.path.abspath(os.expanduser(path))
        # Allow only within project or /tmp
        allowed_prefixes = [
            "/tmp/",
            os.path.expanduser("~"),
            "/home/",
        ]
        # Also check relative paths that stay within project
        resolved = os.path.realpath(abs_path)
        if resolved.startswith("/tmp") or "/home/" in resolved:
            pass  # OK
        else:
            # Check if it's a relative path (stays in project)
            if not path.startswith("/") and ".." not in path:
                pass  # OK - relative path
            else:
                return False, f"Path traversal blocked: {path}"
        # Block specific dangerous paths
        blocked = ["/etc/passwd", "/etc/shadow", "/etc/sudoers", "/.ssh/", "/.gnupg/"]
        for b in blocked:
            if b in abs_path:
                return False, f"Blocked path: {b}"
        return True, ""

    # Tool 1: terminal
    async def _terminal_handler(command: str, timeout: int = 30, workdir: str = None):
        from src.cli_tools import terminal as cli_terminal
        # Sanitize command
        is_safe, err = _sanitize_command(command)
        if not is_safe:
            return ToolResult(tool_name="terminal", success=False, 
                          output=f"Command blocked: {err}", metadata={"blocked": True})
        result = cli_terminal(command, timeout=timeout, workdir=workdir)
        ok = result.get("exit_code", 1) == 0
        return ToolResult(tool_name="terminal", success=ok,
                          output=str(result.get("output", ""))[:4000],
                          metadata=result)

    registry.register(ToolSpec(
        name="terminal",
        description="Execute shell commands in terminal",
        parameters={"command": "Shell command", "timeout": "Timeout seconds", "workdir": "Working directory"},
        handler=_terminal_handler,
    ))

    # Tool 2: read_file
    async def _read_handler(path: str, limit: int = 500):
        from src.cli_tools import read_file as cli_read
        from src.cli_tools import FileReadResult
        # Sanitize path
        is_safe, err = _sanitize_path(path)
        if not is_safe:
            return ToolResult(tool_name="read_file", success=False,
                          output=f"Path blocked: {err}", metadata={"blocked": True})
        result = cli_read(path, offset=1, limit=limit)
        if isinstance(result, FileReadResult):
            return ToolResult(tool_name="read_file", success=True, output=result.content,
                              metadata={"path": result.file_path,
                                        "lines_read": result.total_lines,
                                        "truncated": len(result.content.splitlines()) >= limit})
        # Fallback for dict
        return ToolResult(tool_name="read_file", success=True, output=str(result.get("content", "")),
                          metadata={"path": result.get("file_path", path)})

    registry.register(ToolSpec(
        name="read_file",
        description="Read content from a file",
        parameters={"path": "File path", "limit": "Max lines (default 500)"},
        handler=_read_handler,
    ))

    # Tool 3: write_file
    async def _write_handler(path: str, content: str):
        from src.cli_tools import write_file as cli_write
        # Sanitize path
        is_safe, err = _sanitize_path(path)
        if not is_safe:
            return ToolResult(tool_name="write_file", success=False,
                          output=f"Path blocked: {err}", metadata={"blocked": True})
        result = cli_write(path, content)
        ok = result.get("success", False)
        output = f"File written: {path}" if ok else str(result)
        return ToolResult(tool_name="write_file", success=ok,
                          output=output,
                          metadata={"path": path, "bytes": len(content.encode("utf-8"))})

    registry.register(ToolSpec(
        name="write_file",
        description="Write content to a file",
        parameters={"path": "File path", "content": "Text content"},
        handler=_write_handler,
    ))

    # Tool 4: search_files
    async def _search_handler(pattern: str, path: str = ".", file_glob: str = None, limit: int = 50):
        from src.cli_tools import search_files as cli_search
        result = cli_search(pattern, path=path, file_glob=file_glob, limit=limit)
        matches = result.get("matches", [])
        output = "\n".join(str(m) for m in matches[:100])[:4000]
        return ToolResult(tool_name="search_files", success=True, output=output, metadata=result)

    registry.register(ToolSpec(
        name="search_files",
        description="Search for patterns in files",
        parameters={"pattern": "Regex pattern", "path": "Directory", "file_glob": "Glob pattern", "limit": "Max results"},
        handler=_search_handler,
    ))

    # Tool 5: web_search
    async def _web_handler(query: str, top_k: int = 3):
        from src.cli_tools import web_search as cli_web
        result = cli_web(query, limit=top_k)
        output = str(result.get("results", result))[:4000]
        return ToolResult(tool_name="web_search", success=True, output=output, metadata=result)

    registry.register(ToolSpec(
        name="web_search",
        description="Search the web for information",
        parameters={"query": "Search query", "top_k": "Number of results"},
        handler=_web_handler,
    ))

    # Tool 6: execute_code
    async def _code_handler(code: str, timeout: int = 10):
        from src.cli_tools import execute_code as cli_code
        result = cli_code(code)
        ok = result.get("ok", False)
        return ToolResult(tool_name="execute_code", success=ok,
                          output=str(result),
                          metadata=result)

    registry.register(ToolSpec(
        name="execute_code",
        description="Execute a safe Python code snippet",
        parameters={"code": "Python code string", "timeout": "Seconds"},
        handler=_code_handler,
    ))

    return registry
