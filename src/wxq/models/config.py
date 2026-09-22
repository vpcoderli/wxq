"""Configuration models."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class AppConfig:
    """Application configuration loaded from config.json."""

    db_dir: str
    keys_file: str
    decrypted_dir: str
    decoded_image_dir: str = ""
    wechat_process: str = ""
    wechat_base_dir: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "db_dir": self.db_dir,
            "keys_file": self.keys_file,
            "decrypted_dir": self.decrypted_dir,
            "decoded_image_dir": self.decoded_image_dir,
            "wechat_process": self.wechat_process,
            "wechat_base_dir": self.wechat_base_dir,
        }
