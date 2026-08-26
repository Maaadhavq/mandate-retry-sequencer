#!/usr/bin/env python3
"""PostToolUse: run the test suite after any edit under backend/.

Advisory, not fatal — a failing suite is reported back to Claude so it iterates,
rather than blocking the edit that already happened. CLAUDE.md: every change ships
with something that returns pass/fail.

Stdlib only, so it runs under any Python on PATH. Tests themselves run under the
project's own 3.12 venv.
"""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VENV_PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
VENV_PYTHON_POSIX = ROOT / ".venv" / "bin" / "python"
TIMEOUT_SECONDS = 180


def interpreter() -> str:
    for candidate in (VENV_PYTHON, VENV_PYTHON_POSIX):
        if candidate.exists():
            return str(candidate)
    return sys.executable


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return

    tool_input = payload.get("tool_input") or {}
    tool_response = payload.get("tool_response") or {}
    raw = tool_response.get("filePath") or tool_input.get("file_path") or ""
    if not raw:
        return

    path = raw.replace("\\", "/")
    if "/backend/" not in path or not path.endswith(".py"):
        return

    try:
        result = subprocess.run(
            [interpreter(), "-m", "pytest", "-q", "--no-header", "-x"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        json.dump({"systemMessage": f"Test hook could not run: {exc}"}, sys.stdout)
        return

    if result.returncode == 0:
        return  # Silent on success. The UI only surfaces hooks that have something to say.

    tail = "\n".join((result.stdout + result.stderr).strip().splitlines()[-25:])
    json.dump(
        {
            "decision": "block",
            "reason": f"pytest failed after editing {path}:\n\n{tail}",
            "continue": True,
        },
        sys.stdout,
    )


if __name__ == "__main__":
    main()
