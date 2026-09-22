"""Configuration loading — auto-detect WeChat data directories per platform."""

from __future__ import annotations

import glob as glob_mod
import json
import os
import platform
import sys
from typing import Any

from ..exceptions import ConfigError, DataDirNotFoundError
from ..models.config import AppConfig

_SYSTEM = platform.system().lower()

if _SYSTEM == "linux":
    _DEFAULT_PROCESS = "wechat"
elif _SYSTEM == "darwin":
    _DEFAULT_PROCESS = "WeChat"
else:
    _DEFAULT_PROCESS = "Weixin.exe"

STATE_DIR: str = os.path.expanduser("~/.wxq")
_LEGACY_STATE_DIR: str = os.path.expanduser("~/.wechat-cli")

# Installs from before the rename keep their keys in ~/.wechat-cli. If that
# directory holds a config and the new one doesn't, keep reading the old
# location so an existing install survives the upgrade without re-running init.
# Nothing is moved or deleted — the fallback is read-only.
if not os.path.exists(os.path.join(STATE_DIR, "config.json")) and os.path.exists(
    os.path.join(_LEGACY_STATE_DIR, "config.json")
):
    STATE_DIR = _LEGACY_STATE_DIR

CONFIG_FILE: str = os.path.join(STATE_DIR, "config.json")
KEYS_FILE: str = os.path.join(STATE_DIR, "all_keys.json")


def _choose_candidate(candidates: list[str]) -> str | None:
    """Interactive selection when multiple data directories are found."""
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        if not sys.stdin.isatty():
            return candidates[0]
        print("[!] 检测到多个微信数据目录:")
        for i, c in enumerate(candidates, 1):
            print(f"    {i}. {c}")
        print("    0. 跳过")
        try:
            while True:
                choice = input(f"请选择 [0-{len(candidates)}]: ").strip()
                if choice == "0":
                    return None
                if choice.isdigit() and 1 <= int(choice) <= len(candidates):
                    return candidates[int(choice) - 1]
                print("    无效输入")
        except (EOFError, KeyboardInterrupt):
            print()
            return None
    return None


def _auto_detect_db_dir_windows() -> str | None:
    appdata = os.environ.get("APPDATA", "")
    config_dir = os.path.join(appdata, "Tencent", "xwechat", "config")
    if not os.path.isdir(config_dir):
        return None
    data_roots: list[str] = []
    for ini_file in glob_mod.glob(os.path.join(config_dir, "*.ini")):
        try:
            content: str | None = None
            for enc in ("utf-8", "gbk"):
                try:
                    with open(ini_file, "r", encoding=enc) as f:
                        content = f.read(1024).strip()
                    break
                except UnicodeDecodeError:
                    continue
            if not content or any(c in content for c in "\n\r\x00"):
                continue
            if os.path.isdir(content):
                data_roots.append(content)
        except OSError:
            continue
    seen: set[str] = set()
    candidates: list[str] = []
    for root in data_roots:
        pattern = os.path.join(root, "xwechat_files", "*", "db_storage")
        for match in glob_mod.glob(pattern):
            normalized = os.path.normcase(os.path.normpath(match))
            if os.path.isdir(match) and normalized not in seen:
                seen.add(normalized)
                candidates.append(match)
    return _choose_candidate(candidates)


def _auto_detect_db_dir_linux() -> str | None:
    seen: set[str] = set()
    candidates: list[str] = []
    search_roots = [os.path.expanduser("~/Documents/xwechat_files")]
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user:
        try:
            import pwd
            sudo_home = pwd.getpwnam(sudo_user).pw_dir
        except (KeyError, ImportError):
            sudo_home = None
        if sudo_home:
            fallback = os.path.join(sudo_home, "Documents", "xwechat_files")
            if fallback not in search_roots:
                search_roots.append(fallback)
    for root in search_roots:
        if not os.path.isdir(root):
            continue
        pattern = os.path.join(root, "*", "db_storage")
        for match in glob_mod.glob(pattern):
            normalized = os.path.normcase(os.path.normpath(match))
            if os.path.isdir(match) and normalized not in seen:
                seen.add(normalized)
                candidates.append(match)
    old_path = os.path.expanduser("~/.local/share/weixin/data/db_storage")
    if os.path.isdir(old_path):
        normalized = os.path.normcase(os.path.normpath(old_path))
        if normalized not in seen:
            candidates.append(old_path)

    def _mtime(path: str) -> float:
        msg_dir = os.path.join(path, "message")
        target = msg_dir if os.path.isdir(msg_dir) else path
        try:
            return os.path.getmtime(target)
        except OSError:
            return 0.0

    candidates.sort(key=_mtime, reverse=True)
    return _choose_candidate(candidates)


def _auto_detect_db_dir_macos() -> str | None:
    base = os.path.expanduser(
        "~/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files"
    )
    if not os.path.isdir(base):
        return None
    seen: set[str] = set()
    candidates: list[str] = []
    pattern = os.path.join(base, "*", "db_storage")
    for match in glob_mod.glob(pattern):
        normalized = os.path.normcase(os.path.normpath(match))
        if os.path.isdir(match) and normalized not in seen:
            seen.add(normalized)
            candidates.append(match)
    return _choose_candidate(candidates)


def auto_detect_db_dir() -> str | None:
    """Auto-detect the WeChat db_storage directory for the current platform."""
    if _SYSTEM == "windows":
        return _auto_detect_db_dir_windows()
    if _SYSTEM == "linux":
        return _auto_detect_db_dir_linux()
    if _SYSTEM == "darwin":
        return _auto_detect_db_dir_macos()
    return None


def load_config(config_path: str | None = None) -> AppConfig:
    """Load application configuration.

    Falls back to auto-detection if db_dir is not set.

    Raises:
        DataDirNotFoundError: If WeChat data directory cannot be found.
        ConfigError: If config file is malformed.
    """
    if config_path is None:
        config_path = CONFIG_FILE

    cfg: dict[str, Any] = {}
    if os.path.exists(config_path):
        try:
            with open(config_path, encoding="utf-8") as f:
                cfg = json.load(f)
        except json.JSONDecodeError as e:
            raise ConfigError(f"配置文件解析失败: {config_path}: {e}") from e

    # Auto-detect db_dir if missing
    db_dir = cfg.get("db_dir", "")
    if not db_dir:
        detected = auto_detect_db_dir()
        if detected:
            cfg["db_dir"] = detected
        else:
            raise DataDirNotFoundError(
                "未找到微信数据目录。\n请运行: wxq init"
            )

    state_dir = os.path.dirname(os.path.abspath(config_path))
    cfg.setdefault("keys_file", os.path.join(state_dir, "all_keys.json"))
    cfg.setdefault("decrypted_dir", os.path.join(state_dir, "decrypted"))
    cfg.setdefault("decoded_image_dir", os.path.join(state_dir, "decoded_images"))
    cfg.setdefault("wechat_process", _DEFAULT_PROCESS)

    # Ensure absolute paths
    for key in ("db_dir", "keys_file", "decrypted_dir", "decoded_image_dir"):
        if key in cfg and not os.path.isabs(cfg[key]):
            cfg[key] = os.path.join(state_dir, cfg[key])

    # Derive wechat_base_dir
    db_dir = cfg.get("db_dir", "")
    if db_dir and os.path.basename(db_dir) == "db_storage":
        cfg["wechat_base_dir"] = os.path.dirname(db_dir)
    else:
        cfg["wechat_base_dir"] = db_dir

    return AppConfig(
        db_dir=cfg["db_dir"],
        keys_file=cfg["keys_file"],
        decrypted_dir=cfg["decrypted_dir"],
        decoded_image_dir=cfg.get("decoded_image_dir", ""),
        wechat_process=cfg.get("wechat_process", ""),
        wechat_base_dir=cfg.get("wechat_base_dir", ""),
    )
