import pytest
from src.tools.tool_registry import ToolRegistry, ToolSpec, ToolResult, build_default_registry


@pytest.mark.asyncio
async def test_registry_execute_unknown_tool():
    registry = ToolRegistry()
    result = await registry.execute("nonexistent", {})
    assert result.success is False
    assert "not found" in result.output


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
