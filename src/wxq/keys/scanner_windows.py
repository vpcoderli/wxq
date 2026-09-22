"""Windows key extraction — scan Weixin.exe process memory via ctypes.

Uses kernel32.ReadProcessMemory / VirtualQueryEx to enumerate and read
readable committed memory regions of each Weixin.exe process.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import re
import subprocess
import time
from typing import Any, Callable

from ..exceptions import (
    KeyScanError,
    NoKeysExtractedError,
    PermissionError_,
    ProcessNotFoundError,
)
from .common import collect_db_files, cross_verify_keys, save_results, scan_memory_for_keys

# --- Win32 constants ---
MEM_COMMIT = 0x1000
PROCESS_VM_READ = 0x0010
PROCESS_QUERY_INFORMATION = 0x0400
READABLE_PROTECTIONS = {0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80}
MAX_REGION_SIZE = 500 * 1024 * 1024  # Skip regions > 500 MB
MAX_USER_ADDRESS = 0x7FFFFFFFFFFF

# Lazy kernel32 reference (only resolved on Windows)
_kernel32: Any = None


def _get_kernel32() -> Any:
    """Get the kernel32 DLL handle (cached)."""
    global _kernel32
    if _kernel32 is None:
        _kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    return _kernel32


class _MEMORY_BASIC_INFORMATION(ctypes.Structure):
    """MEMORY_BASIC_INFORMATION for 64-bit Windows."""

    _fields_ = [
        ("BaseAddress", ctypes.c_uint64),
        ("AllocationBase", ctypes.c_uint64),
        ("AllocationProtect", wt.DWORD),
        ("_pad1", wt.DWORD),
        ("RegionSize", ctypes.c_uint64),
        ("State", wt.DWORD),
        ("Protect", wt.DWORD),
        ("Type", wt.DWORD),
        ("_pad2", wt.DWORD),
    ]


def _get_weixin_pids(
    specified_pid: int | None = None,
    print_fn: Callable[..., Any] = print,
) -> list[tuple[int, int]]:
    """Get all Weixin.exe PIDs sorted by memory usage (descending).

    Returns:
        List of (pid, memory_kb) tuples.

    Raises:
        ProcessNotFoundError: If no Weixin.exe processes are found.
    """
    if specified_pid is not None:
        return [(specified_pid, 0)]

    try:
        result = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq Weixin.exe", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        raise KeyScanError(f"Failed to list processes: {e}")

    pids: list[tuple[int, int]] = []
    for line in result.stdout.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        parts = line.strip('"').split('","')
        if len(parts) >= 5:
            try:
                pid = int(parts[1])
                mem_str = parts[4].replace(",", "").replace(" K", "").strip()
                mem_kb = int(mem_str) if mem_str else 0
                pids.append((pid, mem_kb))
            except (ValueError, IndexError):
                continue

    if not pids:
        raise ProcessNotFoundError("Weixin.exe is not running")

    pids.sort(key=lambda x: x[1], reverse=True)
    for pid, mem_kb in pids:
        print_fn(f"[+] Weixin.exe PID={pid} ({mem_kb // 1024}MB)")
    return pids


def _read_process_memory(handle: int, addr: int, size: int) -> bytes | None:
    """Read a block from the target process's virtual memory."""
    kernel32 = _get_kernel32()
    buf = ctypes.create_string_buffer(size)
    bytes_read = ctypes.c_size_t(0)
    ok = kernel32.ReadProcessMemory(
        handle, ctypes.c_uint64(addr), buf, size, ctypes.byref(bytes_read)
    )
    if ok:
        return buf.raw[: bytes_read.value]
    return None


def _enumerate_regions(handle: int) -> list[tuple[int, int]]:
    """Enumerate readable committed memory regions.

    Returns:
        List of (base_address, region_size) tuples.
    """
    kernel32 = _get_kernel32()
    regions: list[tuple[int, int]] = []
    addr = 0
    mbi = _MEMORY_BASIC_INFORMATION()
    mbi_size = ctypes.sizeof(mbi)

    while addr < MAX_USER_ADDRESS:
        ret = kernel32.VirtualQueryEx(
            handle, ctypes.c_uint64(addr), ctypes.byref(mbi), mbi_size
        )
        if ret == 0:
            break
        if (
            mbi.State == MEM_COMMIT
            and mbi.Protect in READABLE_PROTECTIONS
            and 0 < mbi.RegionSize < MAX_REGION_SIZE
        ):
            regions.append((mbi.BaseAddress, mbi.RegionSize))

        next_addr = mbi.BaseAddress + mbi.RegionSize
        if next_addr <= addr:
            break
        addr = next_addr

    return regions


def extract_keys(
    db_dir: str,
    output_path: str,
    pid: int | None = None,
    print_fn: Callable[..., Any] = print,
) -> dict[str, str]:
    """Extract Windows WeChat database encryption keys.

    Scans all Weixin.exe processes' memory for hex key patterns
    and validates them against database file salts.

    Args:
        db_dir: WeChat database directory.
        output_path: Path to write the extracted keys JSON.
        pid: Optional specific PID (default: auto-detect all).
        print_fn: Callable for status output.

    Returns:
        Mapping of salt_hex -> enc_key_hex.

    Raises:
        ProcessNotFoundError: If Weixin.exe is not running.
        NoKeysExtractedError: If scan completes without finding keys.
    """
    kernel32 = _get_kernel32()

    print_fn("=" * 60)
    print_fn("  Extract WeChat database keys (Windows)")
    print_fn("=" * 60)

    db_files, salt_to_dbs = collect_db_files(db_dir)
    if not db_files:
        raise KeyScanError(f"No decryptable .db files found in {db_dir}")

    print_fn(f"\nFound {len(db_files)} databases, {len(salt_to_dbs)} unique salts")
    for salt_hex, dbs in sorted(salt_to_dbs.items(), key=lambda x: len(x[1]), reverse=True):
        print_fn(f"  salt {salt_hex}: {', '.join(dbs)}")

    pids = _get_weixin_pids(pid, print_fn)

    hex_re = re.compile(b"x'([0-9a-fA-F]{64,192})'")
    key_map: dict[str, str] = {}
    remaining_salts = set(salt_to_dbs.keys())
    all_hex_matches = 0
    t0 = time.time()

    for pid_val, mem_kb in pids:
        handle = kernel32.OpenProcess(
            PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid_val
        )
        if not handle:
            print_fn(f"[WARN] Cannot open process PID={pid_val}, skipping")
            continue

        try:
            regions = _enumerate_regions(handle)
            total_bytes = sum(s for _, s in regions)
            total_mb = total_bytes / 1024 / 1024
            print_fn(f"\n[*] Scanning PID={pid_val} ({total_mb:.0f}MB, {len(regions)} regions)")

            scanned_bytes = 0
            for reg_idx, (base, size) in enumerate(regions):
                data = _read_process_memory(handle, base, size)
                scanned_bytes += size
                if not data:
                    continue

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
            kernel32.CloseHandle(handle)

        if not remaining_salts:
            print_fn("\n[+] All keys found, skipping remaining processes")
            break

    elapsed = time.time() - t0
    print_fn(f"\nScan complete: {elapsed:.1f}s, {len(pids)} processes, {all_hex_matches} hex patterns")

    cross_verify_keys(db_files, salt_to_dbs, key_map, print_fn)
    return save_results(db_files, salt_to_dbs, key_map, output_path, print_fn)
