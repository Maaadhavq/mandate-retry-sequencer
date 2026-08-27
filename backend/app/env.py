"""Load `.env` into the process environment, once. SPEC §4.3.

`.env.example` has always advertised that an `ANTHROPIC_API_KEY` in `.env` is how you enable
the live agent, but nothing read the file — the key had to be exported into the shell by hand,
which is easy to get wrong and easy to leak into shell history.

Hand-rolled rather than depending on `python-dotenv`: it is fifteen lines, and one fewer
dependency is one fewer thing between a judge and a working clone.

Real environment variables always win. A `.env` is a convenience for local development, not an
override — if someone has deliberately exported a key for this shell, a stale file must not
silently replace it.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Final

DEFAULT_ENV_PATH: Final[Path] = Path(".env")

_loaded = False


def load_env(path: Path | str = DEFAULT_ENV_PATH, *, force: bool = False) -> int:
    """Read `KEY=value` lines into `os.environ`. Returns how many were set.

    Missing file is not an error — running without a key is a supported mode, not a failure.
    """
    global _loaded
    if _loaded and not force:
        return 0

    path = Path(path)
    _loaded = True
    if not path.exists():
        return 0

    applied = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if not key or not value:
            continue
        if os.environ.get(key):
            continue  # a real env var beats the file, always
        os.environ[key] = value
        applied += 1
    return applied
