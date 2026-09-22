"""Output formatting — JSON (AI-friendly) / Text (human-readable)."""

from __future__ import annotations

import json
import sys
from typing import IO, Any


def output_json(data: Any, file: IO[str] | None = None) -> None:
    """Write data as formatted JSON."""
    f = file or sys.stdout
    json.dump(data, f, ensure_ascii=False, indent=2)
    f.write("\n")


def output_text(text: str, file: IO[str] | None = None) -> None:
    """Write text with trailing newline."""
    f = file or sys.stdout
    f.write(text)
    if not text.endswith("\n"):
        f.write("\n")


def output(data: Any, fmt: str = "json", file: IO[str] | None = None) -> None:
    """Format and write data in the requested format.

    Args:
        data: Data to output — dict/list for JSON, str for text.
        fmt: "json" or "text".
        file: Output stream (default: stdout).
    """
    if fmt == "json":
        output_json(data, file)
    else:
        if isinstance(data, str):
            output_text(data, file)
        elif isinstance(data, dict) and "text" in data:
            output_text(data["text"], file)
        else:
            output_json(data, file)
