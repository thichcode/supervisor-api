import pytest

from src.tools.tool_registry import ToolRegistry, ToolSpec, ToolResult, build_default_registry
from src.harness.tool_registry import ToolCategory as HarnessToolCategory, ToolRegistry as HarnessToolRegistry


@pytest.mark.asyncio
async def test_registry_execute_unknown_tool():
    registry = ToolRegistry()
    result = await registry.execute("nonexistent", {})
    assert result.success is False
    assert "not found" in result.output


@pytest.mark.asyncio
async def test_dangerous_tool_requests_approval(monkeypatch):
    from src.harness import tool_registry as tool_registry_module

    registry = HarnessToolRegistry()
    handler_calls = {"count": 0}
    approval_calls = {}

    async def dangerous_handler(command: str, timeout: int = 30, workdir: str | None = None):
        handler_calls["count"] += 1
        return {"ok": True, "command": command}

    registry.register(
        name="terminal",
        description="Execute shell commands in terminal",
        handler=dangerous_handler,
        category=HarnessToolCategory.TERMINAL,
        dangerous=True,
        parameters={
            "type": "object",
            "properties": {
                "command": {"type": "string"},
            },
            "required": ["command"],
        },
    )

    async def fake_create_approval(**kwargs):
        approval_calls.update(kwargs)

        class Approval:
            id = "apr-123"

        return Approval()

    monkeypatch.setattr(tool_registry_module.approval_service, "create_approval", fake_create_approval)

    result = await registry.execute(
        "terminal",
        {"command": "rm -rf /"},
        approval_context={
            "request_id": "req-123",
            "user_id": "thuong",
            "display_name": "Thuong",
            "thread_id": "thread-1",
            "platform": "telegram",
            "chat_type": "private",
            "chat_scope": "dm",
            "group_chat": False,
            "metadata": {"risk_level": "high"},
        },
    )

    assert result["pending_approval"] is True
    assert result["approval_id"] == "apr-123"
    assert handler_calls["count"] == 0
    assert approval_calls["metadata"]["tool_name"] == "terminal"
    assert approval_calls["metadata"]["dangerous"] is True
    assert approval_calls["metadata"]["thread_id"] == "thread-1"
    assert approval_calls["metadata"]["platform"] == "telegram"
    assert approval_calls["metadata"]["chat_type"] == "private"
    assert approval_calls["metadata"]["chat_scope"] == "dm"
    assert approval_calls["metadata"]["group_chat"] is False


@pytest.mark.asyncio
async def test_dangerous_tool_executes_after_approval(monkeypatch):
    registry = HarnessToolRegistry()
    handler_calls = {"count": 0}

    async def dangerous_handler(command: str, timeout: int = 30, workdir: str | None = None):
        handler_calls["count"] += 1
        return {"ok": True, "command": command}

    registry.register(
        name="terminal",
        description="Execute shell commands in terminal",
        handler=dangerous_handler,
        category=HarnessToolCategory.TERMINAL,
        dangerous=True,
        parameters={
            "type": "object",
            "properties": {
                "command": {"type": "string"},
            },
            "required": ["command"],
        },
    )

    result = await registry.execute("terminal", {"command": "echo ok"}, approved=True)

    assert result == {"ok": True, "command": "echo ok"}
    assert handler_calls["count"] == 1


@pytest.mark.asyncio
async def test_read_file_tool_success(tmp_path):
    from src.tools.tool_registry import _read_file_tool

    test_file = tmp_path / "hello.txt"
    test_file.write_text("line1\nline2\nline3", encoding="utf-8")

    result = await _read_file_tool(str(test_file), limit=10)
    assert result.success is True
    assert "line1" in result.output
    assert result.metadata["lines_read"] == 3


@pytest.mark.asyncio
async def test_read_file_tool_invalid_path():
    from src.tools.tool_registry import _read_file_tool

    result = await _read_file_tool("../etc/passwd")
    assert result.success is False
    assert "Invalid path" in result.output


@pytest.mark.asyncio
async def test_write_file_tool_success(tmp_path):
    from src.tools.tool_registry import _write_file_tool

    target = tmp_path / "out.txt"
    result = await _write_file_tool(str(target), "hello world")
    assert result.success is True
    assert target.read_text(encoding="utf-8") == "hello world"


@pytest.mark.asyncio
async def test_execute_code_tool_safe_math():
    from src.tools.tool_registry import _execute_code_tool

    result = await _execute_code_tool("result = 2 + 2\n", timeout=5)
    assert result.success is True
    assert "4" in result.output


@pytest.mark.asyncio
async def test_execute_code_tool_banned_token():
    from src.tools.tool_registry import _execute_code_tool

    result = await _execute_code_tool("import os\nresult = 1")
    assert result.success is False
    assert "banned" in result.output.lower()


@pytest.mark.asyncio
async def test_execute_code_tool_timeout():
    from src.tools.tool_registry import _execute_code_tool

    result = await _execute_code_tool("while True: pass", timeout=1)
    assert result.success is False
    assert "Timeout" in result.output


@pytest.mark.asyncio
async def test_web_search_tool_stub():
    from src.tools.tool_registry import _web_search_tool

    result = await _web_search_tool("test query", top_k=2)
    assert result.success is True
    assert "stub" in result.output.lower() or "test query" in result.output


class TestDefaultRegistry:
    @pytest.mark.asyncio
    async def test_default_registry_has_expected_tools(self):
        registry = build_default_registry()
        names = {t["name"] for t in registry.list_tools()}
        assert "read_file" in names
        assert "write_file" in names
        assert "web_search" in names
        assert "execute_code" in names

    @pytest.mark.asyncio
    async def test_default_registry_dispatch_read_file(self, tmp_path):
        registry = build_default_registry()
        test_file = tmp_path / "test.txt"
        test_file.write_text("registry content", encoding="utf-8")

        result = await registry.execute("read_file", {"path": str(test_file)})
        assert result.success is True
        assert "registry content" in result.output

    @pytest.mark.asyncio
    async def test_default_registry_dispatch_write_file(self, tmp_path):
        registry = build_default_registry()
        target = tmp_path / "write.txt"
        result = await registry.execute("write_file", {"path": str(target), "content": "ok"})
        assert result.success is True
        assert target.read_text(encoding="utf-8") == "ok"

    @pytest.mark.asyncio
    async def test_default_registry_dispatch_execute_code(self):
        registry = build_default_registry()
        result = await registry.execute("execute_code", {"code": "result = 3 * 7"})
        assert result.success is True
        assert "21" in result.output
