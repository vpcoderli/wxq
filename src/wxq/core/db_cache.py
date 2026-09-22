"""Decrypted database cache — mtime-based invalidation, cross-session reuse."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from typing import Any

from .crypto import full_decrypt, decrypt_wal
from .key_utils import get_key_info
from ..exceptions import DecryptError


class DBCache:
    """Caches decrypted databases in a temp directory.

    Re-decrypts only when the source .db or .db-wal file has changed
    (detected by mtime). Cache metadata is persisted in _mtimes.json
    so it survives across CLI invocations.
    """

    CACHE_DIR: str = os.path.join(tempfile.gettempdir(), "wxq_cache")
    MTIME_FILE: str = os.path.join(
        tempfile.gettempdir(), "wxq_cache", "_mtimes.json"
    )

    def __init__(self, all_keys: dict[str, dict[str, Any]], db_dir: str) -> None:
        self._all_keys = all_keys
        self._db_dir = db_dir
        # rel_key → (db_mtime, wal_mtime, cached_path)
        self._cache: dict[str, tuple[float, float, str]] = {}
        os.makedirs(self.CACHE_DIR, exist_ok=True)
        self._load_persistent_cache()

    def _cache_path(self, rel_key: str) -> str:
        h = hashlib.md5(rel_key.encode()).hexdigest()[:12]
        return os.path.join(self.CACHE_DIR, f"{h}.db")

    def _load_persistent_cache(self) -> None:
        if not os.path.exists(self.MTIME_FILE):
            return
        try:
            with open(self.MTIME_FILE, encoding="utf-8") as f:
                saved: dict[str, Any] = json.load(f)
        except (json.JSONDecodeError, OSError):
            return

        for rel_key, info in saved.items():
            tmp_path = info.get("path", "")
            if not tmp_path or not os.path.exists(tmp_path):
                continue
            rel_path = rel_key.replace("\\", os.sep)
            db_path = os.path.join(self._db_dir, rel_path)
            wal_path = db_path + "-wal"
            try:
                db_mtime = os.path.getmtime(db_path)
                wal_mtime = (
                    os.path.getmtime(wal_path) if os.path.exists(wal_path) else 0.0
                )
            except OSError:
                continue
            if db_mtime == info.get("db_mt") and wal_mtime == info.get("wal_mt"):
                self._cache[rel_key] = (db_mtime, wal_mtime, tmp_path)

    def _save_persistent_cache(self) -> None:
        data: dict[str, dict[str, Any]] = {}
        for rel_key, (db_mt, wal_mt, path) in self._cache.items():
            data[rel_key] = {"db_mt": db_mt, "wal_mt": wal_mt, "path": path}
        try:
            with open(self.MTIME_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f)
        except OSError:
            pass

    def get(self, rel_key: str) -> str | None:
        """Get path to a decrypted database, decrypting on cache miss.

        Args:
            rel_key: Relative path of the database within db_dir
                     (e.g. "contact/contact.db").

        Returns:
            Path to the decrypted database, or None if the key
            is unknown or the source file doesn't exist.
        """
        key_info = get_key_info(self._all_keys, rel_key)
        if not key_info:
            return None

        rel_path = rel_key.replace("\\", "/").replace("/", os.sep)
        db_path = os.path.join(self._db_dir, rel_path)
        wal_path = db_path + "-wal"
        if not os.path.exists(db_path):
            return None

        try:
            db_mtime = os.path.getmtime(db_path)
            wal_mtime = (
                os.path.getmtime(wal_path) if os.path.exists(wal_path) else 0.0
            )
        except OSError:
            return None

        # Cache hit — mtimes match and cached file exists
        if rel_key in self._cache:
            c_db_mt, c_wal_mt, c_path = self._cache[rel_key]
            if (
                c_db_mt == db_mtime
                and c_wal_mt == wal_mtime
                and os.path.exists(c_path)
            ):
                return c_path

        # Cache miss — decrypt
        tmp_path = self._cache_path(rel_key)
        enc_key_hex = str(key_info["enc_key"])
        enc_key = bytes.fromhex(enc_key_hex)

        full_decrypt(db_path, tmp_path, enc_key)
        if os.path.exists(wal_path):
            decrypt_wal(wal_path, tmp_path, enc_key)

        self._cache[rel_key] = (db_mtime, wal_mtime, tmp_path)
        self._save_persistent_cache()
        return tmp_path

    def cleanup(self) -> None:
        """Persist cache metadata. Called at exit."""
        self._save_persistent_cache()
