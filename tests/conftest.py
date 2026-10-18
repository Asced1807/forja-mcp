import textwrap
from collections.abc import Callable
from pathlib import Path

import pytest

MakeProject = Callable[[dict[str, str]], Path]


@pytest.fixture(autouse=True)
def _memoria_aislada(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    """La memoria de forja va a una carpeta temporal: los tests nunca tocan la real."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path_factory.mktemp("xdg")))
    from forja_mcp import server

    server.history._last.clear()


@pytest.fixture
def make_project(tmp_path: Path) -> MakeProject:
    """Crea un proyecto Python de prueba en una carpeta temporal."""

    def make(files: dict[str, str]) -> Path:
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "demo"\nversion = "0"\n')
        for name, body in files.items():
            path = tmp_path / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(textwrap.dedent(body))
        return tmp_path

    return make
