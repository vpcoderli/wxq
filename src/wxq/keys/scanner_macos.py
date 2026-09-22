"""macOS key extraction — scan WeChat process memory via C binary.

The C binary uses task_for_pid / mach_vm_read to read process memory.
If task_for_pid fails, we attempt to re-sign WeChat.app with
get-task-allow entitlement.
"""

from __future__ import annotations

import json
import os
import platform
import plistlib
import subprocess
import sys
import tempfile
from typing import Any

from ..exceptions import (
    KeyScanError,
    NoKeysExtractedError,
    PermissionError_,
    ProcessNotFoundError,
)
from .common import collect_db_files, cross_verify_keys, save_results

_WECHAT_APP_PATHS = [
    "/Applications/WeChat.app",
    os.path.expanduser("~/Applications/WeChat.app"),
]


def _find_binary() -> str:
    """Locate the architecture-appropriate C binary."""
    machine = platform.machine()
    arch_map = {"arm64": "find_all_keys_macos.arm64", "x86_64": "find_all_keys_macos.x86_64"}
    name = arch_map.get(machine)
    if name is None:
        raise KeyScanError(f"Unsupported macOS architecture: {machine}")

    # PyInstaller runtime: bundled under _MEIPASS
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    else:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    search_paths = [
        os.path.join(base, "wxq", "bin", name),
        os.path.join(base, "bin", name),
    ]
    for bin_path in search_paths:
        if os.path.isfile(bin_path):
            return bin_path

    raise KeyScanError(
        f"Key extraction binary not found (searched: {', '.join(search_paths)})\n"
        "Ensure the package is installed correctly."
    )


def _get_original_entitlements(app_path: str) -> dict[str, Any] | None:
    """Extract the app's current code-signing entitlements."""
    try:
        result = subprocess.run(
            ["codesign", "-d", "--entitlements", ":-", app_path],
            capture_output=True,
            timeout=15,
        )
        if result.returncode == 0 and result.stdout:
            return plistlib.loads(result.stdout)  # type: ignore[no-any-return]
    except (subprocess.TimeoutExpired, OSError, plistlib.InvalidFileException):
        pass
    return None


def _build_entitlements_xml(app_path: str) -> bytes:
    """Build entitlements plist: preserve existing + add get-task-allow."""
    entitlements = _get_original_entitlements(app_path) or {}
    entitlements["com.apple.security.get-task-allow"] = True
    return plistlib.dumps(entitlements, fmt=plistlib.FMT_XML)


def _find_wechat_app() -> str | None:
    """Find WeChat.app on disk."""
    for p in _WECHAT_APP_PATHS:
        if os.path.isdir(p):
            return p
    return None


def _resign_wechat(print_fn: Any) -> tuple[bool, str | None]:
    """Re-sign WeChat.app to add get-task-allow entitlement.

    Returns:
        (success, error_message)
    """
    wechat_app = _find_wechat_app()
    if wechat_app is None:
        return False, "WeChat.app not found (searched /Applications and ~/Applications)"

    print_fn(f"\n[*] task_for_pid permission denied — re-signing WeChat...")
    print_fn(f"    Target: {wechat_app}")

    try:
        ent_data = _build_entitlements_xml(wechat_app)
    except Exception as e:
        return False, f"Failed to extract WeChat entitlements: {e}"

    ent_fd, ent_path = tempfile.mkstemp(suffix=".plist")
    try:
        with os.fdopen(ent_fd, "wb") as f:
            f.write(ent_data)

        result = subprocess.run(
            ["codesign", "--force", "--sign", "-", "--entitlements", ent_path, wechat_app],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        return False, "codesign timed out"
    except OSError as e:
        return False, f"codesign failed: {e}"
    finally:
        try:
            os.unlink(ent_path)
        except OSError:
            pass

    if result.returncode != 0:
        return False, f"codesign failed: {result.stderr.strip()}"

    print_fn("[+] Re-signing complete (original entitlements preserved, debug access added).")
    print_fn("[+] Please restart WeChat, then run init again.")
    print_fn("[!] Note: WeChat auto-updates may require re-signing.")
    return True, None


def _handle_task_for_pid_failure(print_fn: Any) -> None:
    """Handle task_for_pid failure by attempting re-sign."""
    print_fn("\n[!] task_for_pid failed: macOS security policy blocked memory access.")
    print_fn("[!] Re-signing WeChat to allow debug access (does not affect functionality).")

    ok, err = _resign_wechat(print_fn)
    if ok:
        raise PermissionError_(
            "WeChat has been re-signed. Please:\n"
            "  1. Quit WeChat completely (not just minimize)\n"
            "  2. Re-open WeChat and log in\n"
            "  3. Run again: sudo wxq init"
        )
    else:
        raise PermissionError_(
            f"Automatic re-signing failed: {err}\n"
            "Manual steps:\n"
            "  # 1. Extract WeChat's current entitlements\n"
            "  codesign -d --entitlements wechat_ent.plist /Applications/WeChat.app\n"
            "  # 2. Add get-task-allow\n"
            '  /usr/libexec/PlistBuddy -c "Add :com.apple.security.get-task-allow bool true" wechat_ent.plist\n'
            "  # 3. Re-sign\n"
            "  codesign --force --sign - --entitlements wechat_ent.plist /Applications/WeChat.app\n"
            "  # 4. Clean up\n"
            "  rm wechat_ent.plist\n"
            "Then restart WeChat and run: sudo wxq init"
        )


def extract_keys(
    db_dir: str,
    output_path: str,
    pid: int | None = None,
    print_fn: Any = print,
) -> dict[str, str]:
    """Extract macOS WeChat database keys via the C binary.

    The C binary runs in the parent directory of db_dir and
    outputs all_keys.json there.

    Args:
        db_dir: WeChat db_storage directory.
        output_path: Path to write the extracted keys JSON.
        pid: Unused (C binary auto-detects WeChat processes).
        print_fn: Callable for status output.

    Returns:
        Mapping of salt_hex → enc_key_hex.

    Raises:
        KeyScanError: If the binary is missing or fails.
        PermissionError_: If task_for_pid fails.
        NoKeysExtractedError: If no keys are found.
    """
    binary = _find_binary()

    # The C binary's working directory must be db_storage's parent
    work_dir = os.path.dirname(db_dir)
    if not os.path.isdir(work_dir):
        raise KeyScanError(f"WeChat data directory does not exist: {work_dir}")

    print_fn(f"[+] Using C binary: {binary}")
    print_fn(f"[+] Working directory: {work_dir}")

    try:
        result = subprocess.run(
            [binary],
            cwd=work_dir,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        raise KeyScanError("Key extraction timed out (120s)")
    except PermissionError:
        raise KeyScanError(
            f"Cannot execute {binary}\n"
            f"Please set execute permission: chmod +x {binary}"
        )
    except OSError as e:
        raise KeyScanError(f"Failed to run binary: {e}")

    if result.stdout:
        print_fn(result.stdout)
    if result.stderr:
        print_fn(result.stderr)

    # Detect task_for_pid failure → attempt re-sign
    combined_output = (result.stdout or "") + (result.stderr or "")
    if "task_for_pid" in combined_output:
        _handle_task_for_pid_failure(print_fn)

    # The C binary outputs all_keys.json to work_dir
    c_output = os.path.join(work_dir, "all_keys.json")
    if not os.path.exists(c_output):
        raise NoKeysExtractedError(
            "C binary did not produce a key file.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    # Read and copy to output_path
    with open(c_output, encoding="utf-8") as f:
        keys_data: dict[str, Any] = json.load(f)

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(keys_data, f, indent=2, ensure_ascii=False)

    # Clean up C binary's temporary output
    if os.path.abspath(c_output) != os.path.abspath(output_path):
        try:
            os.remove(c_output)
        except OSError:
            pass

    # Build salt → key mapping
    key_map: dict[str, str] = {}
    for _rel, info in keys_data.items():
        if isinstance(info, dict) and "enc_key" in info and "salt" in info:
            key_map[info["salt"]] = info["enc_key"]

    print_fn(f"\n[+] Extracted {len(key_map)} keys, saved to: {output_path}")
    if not key_map:
        raise NoKeysExtractedError("C binary ran successfully but found no keys")
    return key_map
