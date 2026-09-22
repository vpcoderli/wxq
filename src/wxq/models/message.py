"""Message domain models."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum


class MessageType(IntEnum):
    """Known WeChat base message types."""

    TEXT = 1
    IMAGE = 3
    VOICE = 34
    CARD = 42
    VIDEO = 43
    STICKER = 47
    LOCATION = 48
    APP_MSG = 49  # links, files, mini-programs, quotes
    VOIP = 50
    SYSTEM = 10000
    REVOKE = 10002

    @classmethod
    def label(cls, raw_type: int) -> str:
        _LABELS: dict[int, str] = {
            1: "文本", 3: "图片", 34: "语音", 42: "名片",
            43: "视频", 47: "表情", 48: "位置", 49: "链接/文件",
            50: "通话", 10000: "系统", 10002: "撤回",
        }
        return _LABELS.get(raw_type, f"type={raw_type}")


# Filter name → (base_type,) or (base_type, sub_type)
MSG_TYPE_FILTERS: dict[str, tuple[int, ...]] = {
    "text": (1,),
    "image": (3,),
    "voice": (34,),
    "video": (43,),
    "sticker": (47,),
    "location": (48,),
    "link": (49,),
    "file": (49, 6),
    "call": (50,),
    "system": (10000,),
}


@dataclass(slots=True)
class Message:
    """A single chat message."""

    local_id: int
    local_type: int
    create_time: int
    sender_label: str
    text: str
    base_type: int = 0
    sub_type: int = 0

    @property
    def time_str(self) -> str:
        from datetime import datetime
        return datetime.fromtimestamp(self.create_time).strftime("%Y-%m-%d %H:%M")

    def format_line(self) -> str:
        if self.sender_label:
            return f"[{self.time_str}] {self.sender_label}: {self.text}"
        return f"[{self.time_str}] {self.text}"


@dataclass(slots=True)
class MessageTableInfo:
    """Reference to a message table inside a decrypted DB."""

    db_path: str
    table_name: str
    max_create_time: int = 0


@dataclass(slots=True)
class ChatContext:
    """Resolved chat target with its message tables."""

    query: str
    username: str
    display_name: str
    is_group: bool
    message_tables: list[MessageTableInfo] = field(default_factory=list)

    @property
    def db_path(self) -> str | None:
        if self.message_tables:
            return self.message_tables[0].db_path
        return None

    @property
    def table_name(self) -> str | None:
        if self.message_tables:
            return self.message_tables[0].table_name
        return None

    def to_dict(self) -> dict[str, object]:
        return {
            "query": self.query,
            "username": self.username,
            "display_name": self.display_name,
            "is_group": self.is_group,
            "db_path": self.db_path,
            "table_name": self.table_name,
        }


@dataclass(frozen=True, slots=True)
class HourlyDistribution:
    """24-hour message distribution."""

    counts: dict[int, int] = field(default_factory=dict)

    def get(self, hour: int) -> int:
        return self.counts.get(hour, 0)

    def to_dict(self) -> dict[int, int]:
        return {h: self.counts.get(h, 0) for h in range(24)}


@dataclass(slots=True)
class SenderStat:
    """Sender ranking entry."""

    name: str
    count: int


@dataclass(slots=True)
class ChatStats:
    """Aggregated chat statistics."""

    total: int = 0
    type_breakdown: dict[str, int] = field(default_factory=dict)
    top_senders: list[SenderStat] = field(default_factory=list)
    hourly: HourlyDistribution = field(default_factory=HourlyDistribution)

    def to_dict(self) -> dict[str, object]:
        return {
            "total": self.total,
            "type_breakdown": self.type_breakdown,
            "top_senders": [{"name": s.name, "count": s.count} for s in self.top_senders],
            "hourly": self.hourly.to_dict(),
        }
