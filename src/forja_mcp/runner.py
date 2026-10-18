"""Ejecución de comandos externos con tiempo límite."""

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Result:
    exit_code: int | None  # None = no se pudo ejecutar o se agotó el tiempo
    stdout: str
    stderr: str
    note: str | None = None


async def run(
    cmd: list[str], cwd: Path, timeout: float, env: dict[str, str] | None = None
) -> Result:
    env = {**os.environ, "NO_COLOR": "1", "PYTHONDONTWRITEBYTECODE": "1", **(env or {})}
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=cwd,
            env=env,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return Result(None, "", "", note=f"no se encontró el ejecutable: {cmd[0]}")

    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return Result(None, "", "", note=f"tiempo agotado tras {timeout:.0f}s")

    return Result(
        proc.returncode,
        out.decode(errors="replace"),
        err.decode(errors="replace"),
    )
