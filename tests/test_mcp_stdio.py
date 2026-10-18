"""El servidor hablando MCP de verdad por stdio, como lo usa Claude Code."""

import asyncio
import os
import sys
from pathlib import Path

from mcp import Client
from mcp.client.stdio import StdioServerParameters, get_default_environment

SERVER = Path(sys.executable).parent / "forja-mcp"


def test_tools_over_stdio(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "demo"\nversion = "0"\n')
    (tmp_path / "test_demo.py").write_text("def test_falla():\n    assert 1 + 1 == 3\n")

    async def scenario() -> None:
        # el cliente arranca el servidor con un entorno mínimo: hay que pasarle la memoria aislada
        env = {**get_default_environment(), "XDG_DATA_HOME": os.environ["XDG_DATA_HOME"]}
        async with Client(StdioServerParameters(command=str(SERVER), env=env)) as client:
            tools = await client.list_tools()
            assert {t.name for t in tools.tools} == {"overview", "outline", "check", "fix", "test", "gate", "mutate", "recall", "learn"}

            result = await client.call_tool("test", {"path": str(tmp_path)})
            data = result.structured_content
            assert data is not None
            assert data["ok"] is False
            assert (data["failures"][0]["file"], data["failures"][0]["line"]) == ("test_demo.py", 2)

            missing = await client.call_tool("check", {"path": str(tmp_path / "no-existe")})
            assert missing.is_error

    asyncio.run(scenario())
