"""Application context — holds shared state for a single session."""

from __future__ import annotations

import atexit
import json
import os
from typing import Any, Callable

from .config import load_config, STATE_DIR
from .db_cache import DBCache
from .key_utils import strip_key_metadata
from .contacts import ContactStore
from ..exceptions import KeysNotFoundError
from ..models.config import AppConfig


class AppContext:
    """Session-scoped container for configuration, cache, and contacts.

    Created once per CLI invocation or MCP session. All services
    receive this as their dependency.
    """

    def __init__(self, config_path: str | None = None) -> None:
        self.config: AppConfig = load_config(config_path)

        if not os.path.exists(self.config.keys_file):
            raise KeysNotFoundError(
                f"密钥文件不存在: {self.config.keys_file}\n"
                "请运行: wxq init"
            )

        with open(self.config.keys_file, encoding="utf-8") as f:
            self.all_keys: dict[str, dict[str, Any]] = strip_key_metadata(json.load(f))

        self.cache = DBCache(self.all_keys, self.config.db_dir)
        atexit.register(self.cache.cleanup)

        self.contacts = ContactStore(
            cache=self.cache,
            decrypted_dir=self.config.decrypted_dir,
            db_dir=self.config.db_dir,
        )

        # Lazily discover message DB keys
        self._msg_db_keys: list[str] | None = None

        os.makedirs(STATE_DIR, exist_ok=True)

    @property
    def msg_db_keys(self) -> list[str]:
        """Keys for message_N.db files, discovered lazily."""
        if self._msg_db_keys is None:
            from ..services.message_service import find_msg_db_keys
            self._msg_db_keys = find_msg_db_keys(self.all_keys)
        return self._msg_db_keys

    @property
    def db_dir(self) -> str:
        return self.config.db_dir

    @property
    def decrypted_dir(self) -> str:
        return self.config.decrypted_dir

    def display_name_fn(self, username: str, names: dict[str, str]) -> str:
        """Display name resolver — delegates to ContactStore."""
        return self.contacts.display_name_for(username)
