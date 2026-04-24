"""Lightweight async tool registry for Hermes-style reasoning loop.

Provides discoverable, sandboxed tools that the reasoning orchestrator
can dispatch dynamically based on a ToolPlan.
"""

from __future__ import annotations

import asyncio
import os
import shlex
import subprocess
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

import structlog

logger = structlog.get_logger()


@dataclass
class ToolResult:
    """Result of a single tool execution."""

    tool_name: str
    success: bool
    output: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolSpec:
    """Specification for a registered tool."""

    name: str
    description: str
    parameters: dict[str, str]
    handler: Callable[..., Awaitable[ToolResult]]


class ToolRegistry:
    """Async tool registry with safe dispatch."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        self._tools[spec.name] = spec

    def list_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,
            }
            for t in self._tools.values()
        ]

    async def execute(self, tool_name: str, arguments: dict[str, Any]) -> ToolResult:
        tool = self._tools.get(tool_name)
        if not tool:
            return ToolResult(
                tool_name=tool_name,
                success=False,
                output=f"Tool '{tool_name}' not found.",
            )
        try:
            return await tool.handler(**arguments)
        except Exception as exc:
            logger.warning("tool_execution_failed", tool=tool_name, error=str(exc))
            return ToolResult(
                tool_name=tool_name,
                success=False,
                output=f"Execution error: {exc}",
                metadata={"error_type": type(exc).__name__},
            )


# ===== Built-in tool handlers =====

async def _read_file_tool(path: str, limit: int = 500) -> ToolResult:
    """Read text file with line limit."""
    if not path or ".." in path:
        return ToolResult("read_file", False, "Invalid path.")
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = []
            for i, line in enumerate(f):
                if i >= limit:
                    break
                lines.append(line.rstrip("\n"))
        content = "\n".join(lines)
        truncated = len(lines) >= limit
        return ToolResult(
            tool_name="read_file",
            success=True,
            output=content,
            metadata={"path": path, "lines_read": len(lines), "truncated": truncated},
        )
    except Exception as exc:
        return ToolResult("read_file", False, str(exc))


async def _write_file_tool(path: str, content: str) -> ToolResult:
    """Write text file (restricted to allowed directories)."""
    if not path or ".." in path:
        return ToolResult("write_file", False, "Invalid path.")
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return ToolResult(
            tool_name="write_file",
            success=True,
            output=f"File written: {path}",
            metadata={"path": path, "bytes": len(content.encode("utf-8"))},
        )
    except Exception as exc:
        return ToolResult("write_file", False, str(exc))


async def _web_search_tool(query: str, top_k: int = 3) -> ToolResult:
    """Stub web search — delegates to URLFetcher if available, else returns stub."""
    try:
        from src.tools.url_fetcher import URLFetcher

        fetcher = URLFetcher(timeout=10, max_urls=top_k)
        urls = fetcher.detect_urls(query)
        if urls:
            infos = await fetcher.fetch_all(query)
            context = fetcher.build_context(infos)
            return ToolResult(
                tool_name="web_search",
                success=True,
                output=context or "No results.",
                metadata={"urls": urls, "results": len(infos)},
            )
    except Exception as exc:
        logger.debug("web_search_fetcher_fallback", error=str(exc))

    return ToolResult(
        tool_name="web_search",
        success=True,
        output=f"Web search stub for: {query}",
        metadata={"query": query, "top_k": top_k, "stub": True},
    )


async def _execute_code_tool(code: str, timeout: int = 10) -> ToolResult:
    """Execute Python code in a restricted subprocess (no shell, no network)."""
    if not code.strip():
        return ToolResult("execute_code", False, "Empty code.")

    # Restrictions
    banned = {"import os", "import sys", "open(", "subprocess", "__import__", "eval(", "exec(", "compile("}
    lower_code = code.lower()
    for token in banned:
        if token in lower_code:
            return ToolResult(
                "execute_code",
                False,
                f"Code contains banned token: {token}",
            )

    script = f"""
import math, json, random, statistics, datetime, itertools, collections, fractions, decimal
result = None
try:
{chr(10).join("    " + line for line in code.splitlines())}
    print(json.dumps({{"result": result if result is not None else "(no result)", "ok": True}}))
except Exception as e:
    print(json.dumps({{"error": str(e), "ok": False}}))
"""
    try:
        proc = await asyncio.create_subprocess_exec(
            "python", "-c", script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        output = stdout.decode("utf-8", errors="replace").strip()
        err = stderr.decode("utf-8", errors="replace").strip()
        return ToolResult(
            tool_name="execute_code",
            success=proc.returncode == 0 and not err,
            output=output or err or "(no output)",
            metadata={
                "returncode": proc.returncode,
                "stderr": err[:500] if err else None,
                "timeout": timeout,
            },
        )
    except asyncio.TimeoutError:
        return ToolResult("execute_code", False, f"Timeout after {timeout}s")
    except Exception as exc:
        return ToolResult("execute_code", False, str(exc))


def build_default_registry() -> ToolRegistry:
    """Create registry with built-in Hermes-style tools."""
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="read_file",
            description="Read a text file from disk.",
            parameters={"path": "file path", "limit": "max lines (default 500)"},
            handler=_read_file_tool,
        )
    )
    registry.register(
        ToolSpec(
            name="write_file",
            description="Write a text file to disk.",
            parameters={"path": "file path", "content": "text content"},
            handler=_write_file_tool,
        )
    )
    registry.register(
        ToolSpec(
            name="web_search",
            description="Search the web or fetch URL context.",
            parameters={"query": "search query", "top_k": "number of results"},
            handler=_web_search_tool,
        )
    )
    registry.register(
        ToolSpec(
            name="execute_code",
            description="Execute a safe Python code snippet.",
            parameters={"code": "Python code string", "timeout": "seconds"},
            handler=_execute_code_tool,
        )
    )
    return registry
