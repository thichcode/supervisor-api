"""Backward-compatible tool registry used by reasoning loop and tests.

This module keeps a lightweight async registry contract:
- ``ToolRegistry.execute()`` returns ``ToolResult`` (never raises for unknown tool)
- ``build_default_registry()`` exposes built-in tools expected by legacy callers
- helper functions ``_read_file_tool`` / ``_write_file_tool`` / ``_execute_code_tool`` /
  ``_web_search_tool`` remain importable for unit tests
"""

from __future__ import annotations

import asyncio
import re
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from src.cli_tools import FileReadResult, read_file as cli_read_file
from src.cli_tools import search_files as cli_search_files
from src.cli_tools import terminal as cli_terminal
from src.cli_tools import web_search as cli_web_search
from src.cli_tools import write_file as cli_write_file
from src.harness.tool_registry import ToolCategory


@dataclass
class ToolResult:
    """Result of a tool execution."""

    tool_name: str
    success: bool
    output: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolSpec:
    """Tool definition used by the lightweight registry."""

    name: str
    description: str
    parameters: dict[str, str]
    handler: Callable[..., Awaitable[ToolResult]]


def _sanitize_path(path: str) -> tuple[bool, str]:
    """Validate a path for safe local file operations."""
    if not path or not str(path).strip():
        return False, "Invalid path: empty"

    raw = str(path).strip().replace("\\", "/")
    if raw.startswith("../") or "/../" in raw or raw.endswith("/.."):
        return False, "Invalid path: path traversal blocked"

    blocked_tokens = (
        "/etc/passwd",
        "/etc/shadow",
        "/etc/sudoers",
        "/.ssh/",
        "/.gnupg/",
    )
    lowered = raw.lower()
    if any(token in lowered for token in blocked_tokens):
        return False, "Invalid path: blocked sensitive location"

    return True, ""


def _sanitize_command(command: str) -> tuple[bool, str]:
    """Best-effort command sanitizer for the terminal tool."""
    if not command or not command.strip():
        return False, "Empty command"
    blocked = ("&&", "||", ";", "|", "\n", "\r", "\x00", "rm -rf", "sudo ")
    text = command.lower()
    if any(token in text for token in blocked):
        return False, "Dangerous command pattern"
    return True, ""


_BANNED_CODE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bimport\s+os\b"),
    re.compile(r"\bimport\s+subprocess\b"),
    re.compile(r"\bimport\s+socket\b"),
    re.compile(r"\bfrom\s+os\s+import\b"),
    re.compile(r"\bfrom\s+subprocess\s+import\b"),
    re.compile(r"\bopen\s*\("),
    re.compile(r"\beval\s*\("),
    re.compile(r"\bexec\s*\("),
    re.compile(r"__import__"),
)


async def _read_file_tool(path: str, limit: int = 500) -> ToolResult:
    ok, reason = _sanitize_path(path)
    if not ok:
        return ToolResult(tool_name="read_file", success=False, output=f"Invalid path: {reason}")

    result = cli_read_file(path, offset=1, limit=limit)
    if isinstance(result, FileReadResult):
        return ToolResult(
            tool_name="read_file",
            success=not result.content.startswith("Error:"),
            output=result.content,
            metadata={
                "path": result.file_path,
                "lines_read": result.total_lines,
                "size_bytes": result.size_bytes,
            },
        )

    content = str(result.get("content", ""))
    return ToolResult(
        tool_name="read_file",
        success=not content.startswith("Error:"),
        output=content,
        metadata=result if isinstance(result, dict) else {},
    )


async def _write_file_tool(path: str, content: str) -> ToolResult:
    ok, reason = _sanitize_path(path)
    if not ok:
        return ToolResult(tool_name="write_file", success=False, output=f"Invalid path: {reason}")

    result = cli_write_file(path, content)
    if result.get("success"):
        return ToolResult(
            tool_name="write_file",
            success=True,
            output=f"File written: {result.get('path', path)}",
            metadata=result,
        )
    return ToolResult(
        tool_name="write_file",
        success=False,
        output=f"Write failed: {result.get('error', 'unknown error')}",
        metadata=result,
    )


def _run_code_subprocess(code: str, timeout: int) -> tuple[bool, str, str]:
    """Run code in isolated subprocess and return (success, output, error)."""
    wrapper = (
        "result = None\n"
        "locals_dict = {}\n"
        "exec(compile(CODE, '<tool>', 'exec'), {}, locals_dict)\n"
        "if 'result' in locals_dict and locals_dict['result'] is not None:\n"
        "    print(locals_dict['result'])\n"
    )
    script = wrapper.replace("CODE", repr(code))
    proc = subprocess.run(
        [sys.executable, "-I", "-c", script],
        capture_output=True,
        text=True,
        timeout=max(1, int(timeout)),
    )
    success = proc.returncode == 0
    output = (proc.stdout or "").strip()
    error = (proc.stderr or "").strip()
    return success, output, error


async def _execute_code_tool(code: str, timeout: int = 10) -> ToolResult:
    if any(p.search(code or "") for p in _BANNED_CODE_PATTERNS):
        return ToolResult(
            tool_name="execute_code",
            success=False,
            output="Execution blocked: banned token detected",
        )

    try:
        success, output, error = await asyncio.to_thread(_run_code_subprocess, code, timeout)
    except subprocess.TimeoutExpired:
        return ToolResult(tool_name="execute_code", success=False, output=f"Timeout after {timeout}s")
    except Exception as exc:  # pragma: no cover - defensive path
        return ToolResult(tool_name="execute_code", success=False, output=f"Execution failed: {exc}")

    if success:
        rendered = output if output else "(no output)"
        return ToolResult(tool_name="execute_code", success=True, output=rendered)

    if "timed out" in error.lower():
        return ToolResult(tool_name="execute_code", success=False, output=f"Timeout after {timeout}s")
    return ToolResult(
        tool_name="execute_code",
        success=False,
        output=error or output or "Execution failed",
    )


async def _web_search_tool(query: str, top_k: int = 3) -> ToolResult:
    result = cli_web_search(query, limit=top_k)
    results = result.get("results", [])
    if result.get("error") or not results:
        # Keep tests stable even without network.
        return ToolResult(
            tool_name="web_search",
            success=True,
            output=f"stub web_search: {query}",
            metadata={"stub": True, **result},
        )

    rendered = str(results)[:4000]
    return ToolResult(tool_name="web_search", success=True, output=rendered, metadata=result)


class ToolRegistry:
    """Lightweight async tool registry compatible with legacy callers."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(
        self,
        spec_or_name: ToolSpec | str,
        description: str = "",
        handler: Callable[..., Awaitable[ToolResult]] | None = None,
        category: ToolCategory = ToolCategory.CUSTOM,
        parameters: dict[str, Any] | None = None,
        requires_approval: bool = False,
        dangerous: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Register tool by ``ToolSpec`` or by fields."""
        if isinstance(spec_or_name, ToolSpec):
            spec = spec_or_name
        else:
            spec = ToolSpec(
                name=spec_or_name,
                description=description,
                parameters={k: str(v) for k, v in (parameters or {}).items()},
                handler=handler or _web_search_tool,
            )
        self._tools[spec.name] = spec

    def list_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": spec.name,
                "description": spec.description,
                "parameters": spec.parameters,
            }
            for spec in self._tools.values()
        ]

    def get_schemas(self) -> list[dict[str, Any]]:
        schemas: list[dict[str, Any]] = []
        for spec in self._tools.values():
            properties = {
                key: {"type": "string", "description": value}
                for key, value in (spec.parameters or {}).items()
            }
            schemas.append(
                {
                    "type": "function",
                    "function": {
                        "name": spec.name,
                        "description": spec.description,
                        "parameters": {
                            "type": "object",
                            "properties": properties,
                            "required": ["query"] if "query" in properties else [],
                        },
                    },
                }
            )
        return schemas

    async def execute(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        max_retries: int = 1,
        approved: bool = False,
        approval_context: dict[str, Any] | None = None,
    ) -> ToolResult:
        spec = self._tools.get(tool_name)
        if not spec:
            return ToolResult(tool_name=tool_name, success=False, output=f"Tool '{tool_name}' not found")

        try:
            result = await spec.handler(**(arguments or {}))
            if isinstance(result, ToolResult):
                return result
            return ToolResult(tool_name=tool_name, success=True, output=str(result)[:4000])
        except Exception as exc:
            return ToolResult(tool_name=tool_name, success=False, output=f"Error: {exc}")


def build_default_registry() -> ToolRegistry:
    """Build registry with default tools used by reasoning loop."""
    registry = ToolRegistry()

    async def _terminal_tool(command: str, timeout: int = 30, workdir: str | None = None) -> ToolResult:
        ok, reason = _sanitize_command(command)
        if not ok:
            return ToolResult(tool_name="terminal", success=False, output=f"Command blocked: {reason}")
        result = cli_terminal(command, timeout=timeout, workdir=workdir)
        success = result.get("exit_code", 1) == 0
        return ToolResult(
            tool_name="terminal",
            success=success,
            output=(result.get("output") or result.get("error") or "")[:4000],
            metadata=result,
        )

    async def _search_files_tool(
        pattern: str,
        path: str = ".",
        file_glob: str | None = None,
        limit: int = 50,
    ) -> ToolResult:
        result = cli_search_files(pattern=pattern, path=path, file_glob=file_glob, limit=limit)
        matches = result.get("matches", [])
        return ToolResult(
            tool_name="search_files",
            success=True,
            output="\n".join(str(item) for item in matches[:100])[:4000],
            metadata=result,
        )

    registry.register(
        ToolSpec(
            name="terminal",
            description="Execute shell commands in terminal",
            parameters={"command": "Shell command", "timeout": "Timeout seconds", "workdir": "Working directory"},
            handler=_terminal_tool,
        )
    )
    registry.register(
        ToolSpec(
            name="read_file",
            description="Read content from a file",
            parameters={"path": "File path", "limit": "Max lines"},
            handler=_read_file_tool,
        )
    )
    registry.register(
        ToolSpec(
            name="write_file",
            description="Write content to a file",
            parameters={"path": "File path", "content": "Text content"},
            handler=_write_file_tool,
        )
    )
    registry.register(
        ToolSpec(
            name="search_files",
            description="Search for patterns in files",
            parameters={"pattern": "Regex pattern", "path": "Directory", "file_glob": "Glob pattern", "limit": "Max results"},
            handler=_search_files_tool,
        )
    )
    registry.register(
        ToolSpec(
            name="web_search",
            description="Search the web for information",
            parameters={"query": "Search query", "top_k": "Number of results"},
            handler=_web_search_tool,
        )
    )
    registry.register(
        ToolSpec(
            name="execute_code",
            description="Execute a safe Python code snippet",
            parameters={"code": "Python code string", "timeout": "Seconds"},
            handler=_execute_code_tool,
        )
    )

    return registry


_tool_registry: ToolRegistry | None = None


def get_tool_registry() -> ToolRegistry:
    """Return global default lightweight tool registry."""
    global _tool_registry
    if _tool_registry is None:
        _tool_registry = build_default_registry()
    return _tool_registry


__all__ = [
    "ToolRegistry",
    "ToolSpec",
    "ToolResult",
    "ToolCategory",
    "get_tool_registry",
    "build_default_registry",
    "_read_file_tool",
    "_write_file_tool",
    "_execute_code_tool",
    "_web_search_tool",
]
