#!/usr/bin/env python3
"""PreToolUse guard: refuse edits to secrets and to generated data.

SPEC §11 and CLAUDE.md. `data/` is produced by generate_data.py and must stay
reproducible from a seed — hand-editing it silently breaks every downstream number.
`.env` must never be written or committed. `.env.example` is explicitly allowed.

Stdlib only, so it runs under any Python on PATH.
"""

import json
import posixpath
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GENERATED_DIR = ROOT / "data"


def deny(reason: str) -> None:
    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        },
        sys.stdout,
    )
    sys.exit(0)


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return  # Malformed payload is not this hook's problem; stay out of the way.

    raw = (payload.get("tool_input") or {}).get("file_path") or ""
    if not raw:
        return

    path = raw.replace("\\", "/")
    name = posixpath.basename(path)

    if name == ".env" or (name.startswith(".env.") and name != ".env.example"):
        deny(
            f"Blocked: {name} holds secrets and is gitignored. "
            "Put the variable in .env.example (without a value) instead."
        )

    # Only the repo-root data/ is generated. A frontend/src/data/ is ordinary source.
    try:
        under_generated_dir = Path(raw).resolve().is_relative_to(GENERATED_DIR)
    except (OSError, ValueError):
        under_generated_dir = False

    if under_generated_dir:
        deny(
            "Blocked: data/ is generated, not authored. Every file there comes from "
            "`python -m backend.scripts.generate_data --seed 42`, and hand-editing it "
            "breaks reproducibility (SPEC §8.1). Change the generator instead."
        )


if __name__ == "__main__":
    main()
