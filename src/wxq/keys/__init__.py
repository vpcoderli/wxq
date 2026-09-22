"""Key extraction — memory scanning for SQLCipher encryption keys.

Platform-specific scanners:
    scanner_macos  — macOS via C binary (task_for_pid / mach_vm_read)
    scanner_windows — Windows via ctypes kernel32
    scanner_linux   — Linux via /proc/<pid>/mem
"""

from __future__ import annotations

import sys
from typing import Any, Callable

from ..exceptions import KeyScanError


def extract_keys(
    db_dir: str,
    output_path: str,
    pid: int | None = None,
    print_fn: Callable[..., Any] = print,
) -> dict[str, str]:
    """Platform-dispatching key extractor.

    Selects the appropriate scanner for the current OS and
    delegates to its extract_keys function.

    Args:
        db_dir: WeChat database directory (db_storage).
        output_path: Path to write the extracted keys JSON.
        pid: Optional specific PID to scan.
        print_fn: Callable for status output.

    Returns:
        Mapping of salt_hex -> enc_key_hex.
    """
    platform = sys.platform
    if platform == "darwin":
        from .scanner_macos import extract_keys as _extract
    elif platform == "win32":
        from .scanner_windows import extract_keys as _extract
    elif platform.startswith("linux"):
        from .scanner_linux import extract_keys as _extract
    else:
        raise KeyScanError(f"Unsupported platform: {platform}")

    return _extract(db_dir, output_path, pid=pid, print_fn=print_fn)
