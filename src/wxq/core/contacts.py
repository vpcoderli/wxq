"""Contact management — loading, caching, fuzzy matching.

Unlike the original which used module-level globals, this uses a
ContactStore class that holds its own cache and can be safely
instantiated per-session or per-request.
"""

from __future__ import annotations

import os
import re
import sqlite3
from contextlib import closing
from typing import Any

from ..models.contact import Contact, ContactDetail, GroupMember, GroupInfo


class ContactStore:
    """Loads and caches contacts from the decrypted contact.db.

    Instance-level caching avoids the stale-global-state bug
    present in the original codebase.
    """

    def __init__(self, cache: Any, decrypted_dir: str, db_dir: str = "") -> None:
        self._cache = cache  # DBCache
        self._decrypted_dir = decrypted_dir
        self._db_dir = db_dir
        self._names: dict[str, str] | None = None
        self._contacts: list[Contact] | None = None
        self._self_username: str | None = None

    def _get_contact_db_path(self) -> str | None:
        """Find the contact.db path, preferring pre-decrypted."""
        pre_decrypted = os.path.join(self._decrypted_dir, "contact", "contact.db")
        if os.path.exists(pre_decrypted):
            return pre_decrypted
        path: str | None = self._cache.get(os.path.join("contact", "contact.db"))
        return path

    def _load_contacts(self) -> None:
        """Load contacts from DB into cache."""
        db_path = self._get_contact_db_path()
        if not db_path:
            self._names = {}
            self._contacts = []
            return

        names: dict[str, str] = {}
        contacts: list[Contact] = []
        try:
            with closing(sqlite3.connect(db_path)) as conn:
                for uname, nick, remark in conn.execute(
                    "SELECT username, nick_name, remark FROM contact"
                ).fetchall():
                    display = remark if remark else nick if nick else uname
                    names[uname] = display
                    contacts.append(Contact(
                        username=uname,
                        nick_name=nick or "",
                        remark=remark or "",
                    ))
        except Exception:
            names = {}
            contacts = []

        self._names = names
        self._contacts = contacts

    def get_names(self) -> dict[str, str]:
        """Get {username: display_name} mapping."""
        if self._names is None:
            self._load_contacts()
        return self._names or {}

    def get_contacts(self) -> list[Contact]:
        """Get full contact list."""
        if self._contacts is None:
            self._load_contacts()
        return self._contacts or []

    def resolve_username(self, chat_name: str) -> str | None:
        """Resolve a display name / username to a canonical username.

        Resolution order: exact username → exact display name (case-insensitive)
        → substring match.
        """
        names = self.get_names()

        # Direct username match
        if chat_name in names or chat_name.startswith("wxid_") or "@chatroom" in chat_name:
            return chat_name

        chat_lower = chat_name.lower()

        # Exact display name match (case-insensitive)
        for uname, display in names.items():
            if chat_lower == display.lower():
                return uname

        # Substring match
        for uname, display in names.items():
            if chat_lower in display.lower():
                return uname

        return None

    def get_self_username(self) -> str:
        """Determine the current user's username from the account directory."""
        if self._self_username is not None:
            return self._self_username

        if not self._db_dir:
            return ""

        names = self.get_names()
        account_dir = os.path.basename(os.path.dirname(self._db_dir))
        candidates = [account_dir]
        m = re.fullmatch(r"(.+)_([0-9a-fA-F]{4,})", account_dir)
        if m:
            candidates.insert(0, m.group(1))

        for candidate in candidates:
            if candidate and candidate in names:
                self._self_username = candidate
                return self._self_username

        return ""

    def display_name_for(self, username: str) -> str:
        """Resolve a username to its display name, handling 'me'."""
        if not username:
            return ""
        if username == self.get_self_username():
            return "me"
        names = self.get_names()
        return names.get(username, username)

    def get_group_members(self, chatroom_username: str) -> GroupInfo:
        """Get group chat member list."""
        db_path = self._get_contact_db_path()
        if not db_path:
            return GroupInfo()

        names = self.get_names()

        try:
            with closing(sqlite3.connect(db_path)) as conn:
                row = conn.execute(
                    "SELECT id FROM contact WHERE username = ?",
                    (chatroom_username,),
                ).fetchone()
                if not row:
                    return GroupInfo()
                room_id = row[0]

                # Get owner
                owner = ""
                owner_username = ""
                owner_row = conn.execute(
                    "SELECT owner FROM chat_room WHERE id = ?", (room_id,)
                ).fetchone()
                if owner_row and owner_row[0]:
                    owner_username = owner_row[0]
                    owner = names.get(owner_username, owner_username)

                # Get member IDs
                member_ids = [
                    r[0]
                    for r in conn.execute(
                        "SELECT member_id FROM chatroom_member WHERE room_id = ?",
                        (room_id,),
                    ).fetchall()
                ]
                if not member_ids:
                    return GroupInfo(owner=owner)

                # Batch query member info
                placeholders = ",".join("?" * len(member_ids))
                members: list[GroupMember] = []
                for uid, username, nick, remark in conn.execute(
                    f"SELECT id, username, nick_name, remark FROM contact "
                    f"WHERE id IN ({placeholders})",
                    member_ids,
                ):
                    display = remark if remark else nick if nick else username
                    members.append(GroupMember(
                        username=username,
                        nick_name=nick or "",
                        remark=remark or "",
                        display_name=display,
                    ))

                # Sort: owner first, then alphabetical
                members.sort(
                    key=lambda m: (
                        0 if m.username == owner_username else 1,
                        m.display_name,
                    )
                )

                return GroupInfo(
                    members=members,
                    owner=owner,
                    member_count=len(members),
                )
        except Exception:
            return GroupInfo()

    def get_contact_detail(self, username: str) -> ContactDetail | None:
        """Get full contact details."""
        db_path = self._get_contact_db_path()
        if not db_path:
            return None

        try:
            with closing(sqlite3.connect(db_path)) as conn:
                row = conn.execute(
                    "SELECT username, nick_name, remark, alias, description, "
                    "small_head_url, big_head_url, verify_flag, local_type "
                    "FROM contact WHERE username = ?",
                    (username,),
                ).fetchone()
                if not row:
                    return None
                uname, nick, remark, alias, desc, small_url, big_url, verify, ltype = row
                return ContactDetail(
                    username=uname,
                    nick_name=nick or "",
                    remark=remark or "",
                    alias=alias or "",
                    description=desc or "",
                    avatar=small_url or big_url or "",
                    verify_flag=verify or 0,
                    local_type=ltype or 0,
                )
        except Exception:
            return None
