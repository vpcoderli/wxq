"""Contact domain models."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class Contact:
    """Minimal contact info used in name resolution."""

    username: str
    nick_name: str = ""
    remark: str = ""

    @property
    def display_name(self) -> str:
        return self.remark or self.nick_name or self.username

    @property
    def is_group(self) -> bool:
        return "@chatroom" in self.username

    @property
    def is_subscription(self) -> bool:
        return self.username.startswith("gh_")


@dataclass(frozen=True, slots=True)
class ContactDetail:
    """Full contact detail from DB."""

    username: str
    nick_name: str = ""
    remark: str = ""
    alias: str = ""
    description: str = ""
    avatar: str = ""
    verify_flag: int = 0
    local_type: int = 0

    @property
    def display_name(self) -> str:
        return self.remark or self.nick_name or self.username

    @property
    def is_group(self) -> bool:
        return "@chatroom" in self.username

    @property
    def is_subscription(self) -> bool:
        return self.username.startswith("gh_")

    def to_dict(self) -> dict[str, object]:
        return {
            "username": self.username,
            "nick_name": self.nick_name,
            "remark": self.remark,
            "alias": self.alias,
            "description": self.description,
            "avatar": self.avatar,
            "verify_flag": self.verify_flag,
            "local_type": self.local_type,
            "is_group": self.is_group,
            "is_subscription": self.is_subscription,
        }


@dataclass(frozen=True, slots=True)
class GroupMember:
    """A member within a group chat."""

    username: str
    nick_name: str = ""
    remark: str = ""
    display_name: str = ""


@dataclass(frozen=True, slots=True)
class GroupInfo:
    """Group chat metadata."""

    members: list[GroupMember] = field(default_factory=list)
    owner: str = ""
    member_count: int = 0
