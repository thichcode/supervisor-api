import pytest

from src import cli_tools


def test_terminal_rejects_empty_command():
    result = cli_tools.terminal("   ")

    assert result["exit_code"] == -1
    assert "empty" in result["error"].lower()


def test_terminal_executes_without_shell_injection_style_input():
    # Should be treated as plain args, not shell control operators.
    result = cli_tools.terminal('python -c "print(123)" && echo hacked')

    assert result["exit_code"] == 0
    assert "123" in result.get("output", "")
    assert "hacked" not in result.get("output", "")


@pytest.mark.asyncio
async def test_terminal_async_runs_command():
    result = await cli_tools.terminal_async('python -c "print(456)"')

    assert result["exit_code"] == 0
    assert "456" in result["output"]
