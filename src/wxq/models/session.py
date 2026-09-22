"""Session (recent conversation) model."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class Session:
    """A recent WeChat conversation session."""

    username: str
    display_name: str
    is_group: bool
    unread_count: int
    last_message: str
    msg_type: str
    timestamp: int
    time_str: str

    def to_dict(self) -> dict[str, object]:
        return {
            "username": self.username,
            "chat": self.display_name,
            "is_group": self.is_group,
            "unread": self.unread_count,
            "last_message": self.last_message,
            "msg_type": self.msg_type,
            "timestamp": self.timestamp,
            "time": self.time_str,
        }
