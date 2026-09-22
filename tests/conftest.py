"""Shared fixtures for wxq tests."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import struct
import tempfile
from contextlib import closing
from typing import Any

import pytest

from wxq.core.crypto import PAGE_SZ, SALT_SZ, RESERVE_SZ, SQLITE_HDR


# ---- Helpers ----


def _md5(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()


# ---- Temporary directory tree ----


@pytest.fixture()
def tmp_dir(tmp_path):
    """A temporary directory for test artifacts."""
    return tmp_path


# ---- Fake SQLCipher database builder ----


def build_encrypted_page(enc_key: bytes, data: bytes, pgno: int) -> bytes:
    """Build a fake encrypted page that decrypt_page can invert.

    This intentionally mirrors crypto.decrypt_page logic so we can
    round-trip test without hitting real SQLCipher.
    """
    from Crypto.Cipher import AES

    iv = os.urandom(16)
    hmac_pad = b"\x00" * 64  # placeholder HMAC (not verified in decrypt)

    if pgno == 1:
        # Page 1: skip first SALT_SZ bytes (salt), encrypt rest up to RESERVE_SZ
        plaintext = data[SALT_SZ : PAGE_SZ - RESERVE_SZ]
        # If data doesn't start with SQLITE_HDR, prepend it
        if data[:len(SQLITE_HDR)] == SQLITE_HDR:
            plaintext = data[SALT_SZ : PAGE_SZ - RESERVE_SZ]
        else:
            plaintext = data[SALT_SZ : PAGE_SZ - RESERVE_SZ]
        # Pad to block boundary
        pad_len = 16 - (len(plaintext) % 16) if len(plaintext) % 16 != 0 else 0
        plaintext = plaintext + b"\x00" * pad_len
        cipher = AES.new(enc_key, AES.MODE_CBC, iv)
        encrypted = cipher.encrypt(plaintext)
        salt = data[:SALT_SZ]
        return salt + encrypted + iv + hmac_pad
    else:
        plaintext = data[: PAGE_SZ - RESERVE_SZ]
        pad_len = 16 - (len(plaintext) % 16) if len(plaintext) % 16 != 0 else 0
        plaintext = plaintext + b"\x00" * pad_len
        cipher = AES.new(enc_key, AES.MODE_CBC, iv)
        encrypted = cipher.encrypt(plaintext)
        return encrypted + iv + hmac_pad


@pytest.fixture()
def fake_encrypted_db(tmp_path):
    """Create a minimal fake encrypted database file.

    Returns (db_path, enc_key_hex, enc_key_bytes).
    """
    enc_key = os.urandom(32)
    enc_key_hex = enc_key.hex()

    # Build page 1 — the SQLite header page
    page1_plain = bytearray(PAGE_SZ)
    page1_plain[:len(SQLITE_HDR)] = SQLITE_HDR
    page1_plain[16:18] = struct.pack(">H", PAGE_SZ)  # page size in header

    page1_enc = build_encrypted_page(enc_key, bytes(page1_plain), 1)

    db_path = str(tmp_path / "test.db")
    with open(db_path, "wb") as f:
        f.write(page1_enc)

    return db_path, enc_key_hex, enc_key


# ---- Plain SQLite DB helpers ----


@pytest.fixture()
def contact_db(tmp_path):
    """Create a test contact.db with some contacts."""
    db_path = str(tmp_path / "contact.db")
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute("""
            CREATE TABLE contact (
                id INTEGER PRIMARY KEY,
                username TEXT,
                nick_name TEXT,
                remark TEXT,
                alias TEXT,
                description TEXT,
                small_head_url TEXT,
                big_head_url TEXT,
                verify_flag INTEGER DEFAULT 0,
                local_type INTEGER DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE TABLE chat_room (
                id INTEGER PRIMARY KEY,
                owner TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE chatroom_member (
                id INTEGER PRIMARY KEY,
                room_id INTEGER,
                member_id INTEGER
            )
        """)

        # Insert test contacts
        conn.executemany(
            "INSERT INTO contact (id, username, nick_name, remark) VALUES (?, ?, ?, ?)",
            [
                (1, "wxid_alice123", "Alice", "小爱"),
                (2, "wxid_bob456", "Bob", ""),
                (3, "group1@chatroom", "Test Group", ""),
                (4, "wxid_charlie789", "Charlie", "查理"),
                (5, "gh_official001", "Official Account", ""),
            ],
        )

        # Insert group room info
        conn.execute(
            "INSERT INTO chat_room (id, owner) VALUES (?, ?)", (3, "wxid_alice123")
        )
        conn.executemany(
            "INSERT INTO chatroom_member (room_id, member_id) VALUES (?, ?)",
            [(3, 1), (3, 2), (3, 4)],
        )

        conn.commit()
    return db_path


@pytest.fixture()
def message_db(tmp_path):
    """Create a test message_0.db with message tables and messages.

    Returns (db_path, table_name, username).
    """
    username = "wxid_alice123"
    table_hash = _md5(username)
    table_name = f"Msg_{table_hash}"

    db_path = str(tmp_path / "message_0.db")
    with closing(sqlite3.connect(db_path)) as conn:
        # Name2Id table
        conn.execute("""
            CREATE TABLE Name2Id (
                rowid INTEGER PRIMARY KEY,
                user_name TEXT
            )
        """)
        conn.executemany(
            "INSERT INTO Name2Id (rowid, user_name) VALUES (?, ?)",
            [(1, "wxid_alice123"), (2, "wxid_bob456"), (3, "wxid_me")],
        )

        # Message table
        conn.execute(f"""
            CREATE TABLE [{table_name}] (
                local_id INTEGER PRIMARY KEY,
                local_type INTEGER,
                create_time INTEGER,
                real_sender_id INTEGER,
                message_content TEXT,
                WCDB_CT_message_content INTEGER DEFAULT 0
            )
        """)

        messages = [
            (1, 1, 1700000000, 1, "Hello from Alice"),
            (2, 1, 1700000060, 3, "Hi Alice!"),
            (3, 1, 1700000120, 1, "How are you?"),
            (4, 3, 1700000180, 1, None),  # Image message
            (5, 49, 1700000240, 2, '<msg><appmsg appid="" sdkver=""><title>Test Link</title><type>5</type><url>https://example.com</url></appmsg></msg>'),
        ]
        conn.executemany(
            f"INSERT INTO [{table_name}] (local_id, local_type, create_time, real_sender_id, message_content, WCDB_CT_message_content) VALUES (?, ?, ?, ?, ?, 0)",
            messages,
        )
        conn.commit()

    return db_path, table_name, username


@pytest.fixture()
def session_db(tmp_path):
    """Create a test session.db with session data."""
    db_path = str(tmp_path / "session.db")
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute("""
            CREATE TABLE SessionTable (
                username TEXT,
                unread_count INTEGER,
                summary TEXT,
                last_timestamp INTEGER,
                last_msg_type INTEGER,
                last_msg_sender TEXT,
                last_sender_display_name TEXT
            )
        """)
        sessions = [
            ("wxid_alice123", 3, "Hello!", 1700000300, 1, "wxid_alice123", "Alice"),
            ("wxid_bob456", 0, "See you later", 1700000200, 1, "wxid_bob456", "Bob"),
            ("group1@chatroom", 5, "wxid_charlie789:\nMeeting at 3pm", 1700000400, 1, "wxid_charlie789", "Charlie"),
        ]
        conn.executemany(
            "INSERT INTO SessionTable VALUES (?, ?, ?, ?, ?, ?, ?)", sessions
        )
        conn.commit()
    return db_path


@pytest.fixture()
def favorite_db(tmp_path):
    """Create a test favorite.db."""
    db_path = str(tmp_path / "favorite.db")
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute("""
            CREATE TABLE fav_db_item (
                local_id INTEGER PRIMARY KEY,
                type INTEGER,
                update_time INTEGER,
                content TEXT,
                fromusr TEXT,
                realchatname TEXT
            )
        """)
        favs = [
            (1, 1, 1700000000, '<favitem><desc>Bookmarked text note</desc></favitem>', "wxid_alice123", ""),
            (2, 5, 1700000100, '<favitem><pagetitle>Great Article</pagetitle><pagedesc>About AI</pagedesc></favitem>', "wxid_bob456", "group1@chatroom"),
        ]
        conn.executemany(
            "INSERT INTO fav_db_item VALUES (?, ?, ?, ?, ?, ?)", favs
        )
        conn.commit()
    return db_path


# ---- Mock AppContext ----


class MockDBCache:
    """A cache that returns pre-set paths without decryption."""

    def __init__(self, path_map: dict[str, str] | None = None):
        self._map = path_map or {}

    def get(self, rel_key: str) -> str | None:
        normalized = rel_key.replace("\\", "/")
        return self._map.get(normalized)


class MockContactStore:
    """Minimal contact store backed by a real contact.db."""

    def __init__(self, db_path: str):
        self._db_path = db_path
        self._names: dict[str, str] | None = None

    def get_names(self) -> dict[str, str]:
        if self._names is None:
            self._names = {}
            with closing(sqlite3.connect(self._db_path)) as conn:
                for uname, nick, remark in conn.execute(
                    "SELECT username, nick_name, remark FROM contact"
                ).fetchall():
                    self._names[uname] = remark if remark else nick if nick else uname
        return self._names

    def resolve_username(self, name: str) -> str | None:
        names = self.get_names()
        if name in names:
            return name
        for uname, display in names.items():
            if name.lower() == display.lower():
                return uname
        for uname, display in names.items():
            if name.lower() in display.lower():
                return uname
        return None

    def get_contacts(self):
        return []

    def get_contact_detail(self, username):
        return None

    def get_group_members(self, username):
        return {"members": [], "owner": ""}

    def display_name_for(self, username):
        names = self.get_names()
        return names.get(username, username)

    def get_self_username(self):
        return "wxid_me"
