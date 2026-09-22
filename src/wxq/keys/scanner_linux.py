"""Linux key extraction — scan WeChat process memory via /proc.

Reads /proc/<pid>/maps to enumerate readable regions, then
scans /proc/<pid>/mem for hex key patterns. Requires root or
CAP_SYS_PTRACE.
"""

from __future__ import annotations

import os
import re
import time
from typing import Any, Callable

from ..exceptions import (
    KeyScanError,
    NoKeysExtractedError,
    PermissionError_,
    ProcessNotFoundError,
)
from .common import collect_db_files, cross_verify_keys, save_results, scan_memory_for_keys

_KNOWN_COMMS = frozenset({"wechat", "wechatappex", "weixin"})
_INTERPRETER_PREFIXES = ("python", "bash", "sh", "zsh", "node", "perl", "ruby")
_SKIP_MAPPINGS = frozenset({"[vdso]", "[vsyscall]", "[vvar]"})
_SKIP_PATH_PREFIXES = ("/usr/lib/", "/lib/", "/usr/share/")
_WECHAT_KEYWORDS = ("wechat", "weixin", "wcdb")
MAX_REGION_SIZE = 500 * 1024 * 1024


def _safe_readlink(path: str) -> str:
    """Read a symlink, returning empty string on failure."""
    try:
        return os.path.realpath(os.readlink(path))
    except OSError:
        return ""


def _is_wechat_process(pid: int) -> bool:
    """Check whether a PID belongs to a WeChat process."""
    if pid == os.getpid():
        return False
    try:
        with open(f"/proc/{pid}/comm") as f:
            comm = f.read().strip().lower()
        if comm in _KNOWN_COMMS:
            return True
        exe_path = _safe_readlink(f"/proc/{pid}/exe")
        exe_name = os.path.basename(exe_path).lower()
        # Exclude interpreter processes
        if any(exe_name.startswith(p) for p in _INTERPRETER_PREFIXES):
            return False
        return "wechat" in exe_name or "weixin" in exe_name
    except (PermissionError, FileNotFoundError, ProcessLookupError):
        return False


def _get_wechat_pids(
    specified_pid: int | None = None,
    print_fn: Callable[..., Any] = print,
) -> list[tuple[int, int]]:
    """Detect WeChat process PIDs, sorted by RSS descending.

    Returns:
        List of (pid, rss_kb) tuples.

    Raises:
        ProcessNotFoundError: If no WeChat processes are detected.
    """
    if specified_pid is not None:
        return [(specified_pid, 0)]

    pids: list[tuple[int, int]] = []
    for pid_str in os.listdir("/proc"):
        if not pid_str.isdigit():
            continue
        pid = int(pid_str)
        try:
            if not _is_wechat_process(pid):
                continue
            with open(f"/proc/{pid}/statm") as f:
                rss_pages = int(f.read().split()[1])
            rss_kb = rss_pages * 4  # pages are 4 KB
            pids.append((pid, rss_kb))
        except (PermissionError, FileNotFoundError, ProcessLookupError, ValueError):
            continue

    if not pids:
        raise ProcessNotFoundError("No WeChat process detected on Linux")

    pids.sort(key=lambda x: x[1], reverse=True)
    for pid, rss_kb in pids:
        exe_path = _safe_readlink(f"/proc/{pid}/exe")
        print_fn(f"[+] WeChat PID={pid} ({rss_kb // 1024}MB) {exe_path}")
    return pids


def _get_readable_regions(pid: int) -> list[tuple[int, int]]:
    """Parse /proc/<pid>/maps for readable memory regions.

    Skips kernel mappings ([vdso] etc.) and system libraries
    (unless they contain WeChat-related strings).

    Returns:
        List of (start_address, size) tuples.
    """
    regions: list[tuple[int, int]] = []
    with open(f"/proc/{pid}/maps") as f:
        for line in f:
            parts = line.split()
            if len(parts) < 2:
                continue
            # Must be readable
            if "r" not in parts[1]:
                continue
            # Skip kernel and system library mappings
            if len(parts) >= 6:
                mapping_name = parts[5]
                if mapping_name in _SKIP_MAPPINGS:
                    continue
                mapping_lower = mapping_name.lower()
                if any(mapping_name.startswith(p) for p in _SKIP_PATH_PREFIXES):
                    # Keep if it's a WeChat-related library
                    if not any(kw in mapping_lower for kw in _WECHAT_KEYWORDS):
                        continue

            addr_range = parts[0].split("-")
            start = int(addr_range[0], 16)
            end = int(addr_range[1], 16)
            size = end - start
            if 0 < size < MAX_REGION_SIZE:
                regions.append((start, size))

    return regions


def _check_permissions() -> None:
    """Verify we have permission to read process memory.

    Raises:
        PermissionError_: If lacking root or CAP_SYS_PTRACE.
    """
    if os.geteuid() == 0:
        return

    # Check for CAP_SYS_PTRACE in effective capabilities
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("CapEff:"):
                    cap_eff = int(line.split(":")[1].strip(), 16)
                    CAP_SYS_PTRACE = 1 << 19
                    if cap_eff & CAP_SYS_PTRACE:
                        return
                    break
    except (OSError, ValueError):
        pass

    raise PermissionError_(
        "Root privileges or CAP_SYS_PTRACE required to read process memory.\n"
        "Use: sudo wxq init\n"
        "Or grant capability: sudo setcap cap_sys_ptrace=ep $(which python3)"
    )


def extract_keys(
    db_dir: str,
    output_path: str,
    pid: int | None = None,
    print_fn: Callable[..., Any] = print,
) -> dict[str, str]:
    """Extract Linux WeChat database encryption keys.

    Scans WeChat process memory via /proc/<pid>/mem for hex key
    patterns and validates them against database file salts.

    Args:
        db_dir: WeChat database directory.
        output_path: Path to write the extracted keys JSON.
        pid: Optional specific PID (default: auto-detect).
        print_fn: Callable for status output.

    Returns:
        Mapping of salt_hex -> enc_key_hex.

    Raises:
        PermissionError_: If lacking required privileges.
        ProcessNotFoundError: If no WeChat processes found.
        NoKeysExtractedError: If scan completes without finding keys.
    """
    _check_permissions()

    print_fn("=" * 60)
    print_fn("  Extract WeChat database keys (Linux, memory scan)")
    print_fn("=" * 60)

    db_files, salt_to_dbs = collect_db_files(db_dir)
    if not db_files:
        raise KeyScanError(f"No decryptable .db files found in {db_dir}")

    print_fn(f"\nFound {len(db_files)} databases, {len(salt_to_dbs)} unique salts")
    for salt_hex, dbs in sorted(salt_to_dbs.items(), key=lambda x: len(x[1]), reverse=True):
        print_fn(f"  salt {salt_hex}: {', '.join(dbs)}")

    pids = _get_wechat_pids(pid, print_fn)

    hex_re = re.compile(rb"x'([0-9a-fA-F]{64,192})'")
    key_map: dict[str, str] = {}
    remaining_salts = set(salt_to_dbs.keys())
    all_hex_matches = 0
    t0 = time.time()

    for pid_val, rss_kb in pids:
        try:
            regions = _get_readable_regions(pid_val)
        except PermissionError:
            print_fn(f"[WARN] Cannot read /proc/{pid_val}/maps — permission denied, skipping")
            continue
        except (FileNotFoundError, ProcessLookupError):
            print_fn(f"[WARN] PID {pid_val} has exited, skipping")
            continue

        total_bytes = sum(s for _, s in regions)
        total_mb = total_bytes / 1024 / 1024
        print_fn(f"\n[*] Scanning PID={pid_val} ({total_mb:.0f}MB, {len(regions)} regions)")

        try:
            mem_fd = open(f"/proc/{pid_val}/mem", "rb")
        except PermissionError:
            print_fn(f"[WARN] Cannot open /proc/{pid_val}/mem — permission denied, skipping")
            continue
        except (FileNotFoundError, ProcessLookupError):
            print_fn(f"[WARN] PID {pid_val} has exited, skipping")
            continue

        # Re-verify this is still a WeChat process
        if not _is_wechat_process(pid_val):
            print_fn(f"[WARN] PID {pid_val} is no longer a WeChat process, skipping")
            mem_fd.close()
            continue

        scanned_bytes = 0
        try:
            for reg_idx, (base, size) in enumerate(regions):
                try:
                    mem_fd.seek(base)
                    data = mem_fd.read(size)
                except (OSError, ValueError):
                    continue
                scanned_bytes += len(data)

                all_hex_matches += scan_memory_for_keys(
                    data, hex_re, db_files, salt_to_dbs,
                    key_map, remaining_salts, base, pid_val, print_fn,
                )

                if (reg_idx + 1) % 200 == 0:
                    elapsed = time.time() - t0
                    progress = scanned_bytes / total_bytes * 100 if total_bytes else 100
                    print_fn(
                        f"  [{progress:.1f}%] {len(key_map)}/{len(salt_to_dbs)} salts matched, "
                        f"{all_hex_matches} hex patterns, {elapsed:.1f}s"
                    )
        finally:
            mem_fd.close()

        if not remaining_salts:
            print_fn("\n[+] All keys found, skipping remaining processes")
            break

    elapsed = time.time() - t0
    print_fn(f"\nScan complete: {elapsed:.1f}s, {len(pids)} processes, {all_hex_matches} hex patterns")

    cross_verify_keys(db_files, salt_to_dbs, key_map, print_fn)
    return save_results(db_files, salt_to_dbs, key_map, output_path, print_fn)
