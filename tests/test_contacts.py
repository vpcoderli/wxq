"""Tests for ContactStore."""

import pytest

from tests.conftest import MockDBCache
from wxq.core.contacts import ContactStore


class TestContactStore:
    def test_get_names(self, contact_db):
        cache = MockDBCache({"contact/contact.db": contact_db})
        store = ContactStore(cache=cache, decrypted_dir="/nonexistent")
        names = store.get_names()
        assert names["wxid_alice123"] == "小爱"  # remark takes precedence
        assert names["wxid_bob456"] == "Bob"     # nick_name when no remark
        assert names["group1@chatroom"] == "Test Group"

    def test_get_names_caches(self, contact_db):
        cache = MockDBCache({"contact/contact.db": contact_db})
        store = ContactStore(cache=cache, decrypted_dir="/nonexistent")
        names1 = store.get_names()
        names2 = store.get_names()
        assert names1 is names2  # same object (cached)

    def test_resolve_username_exact(self, contact_db):
        cache = MockDBCache({"contact/contact.db": contact_db})
        store = ContactStore(cache=cache, decrypted_dir="/nonexistent")
        assert store.resolve_username("wxid_alice123") == "wxid_alice123"

    def test_resolve_username_by_display_name(self, contact_db):
        cache = MockDBCache({"contact/contact.db": contact_db})
        store = ContactStore(cache=cache, decrypted_dir="/nonexistent")
        assert store.resolve_username("小爱") == "wxid_alice123"

    def test_resolve_username_case_insensitive(self, contact_db):
        cache = MockDBCache({"contact/contact.db": contact_db})
        store = ContactStore(cache=cache, decrypted_dir="/nonexistent")
        assert store.resolve_username("bob") == "wxid_bob456"

    def test_resolve_username_substring(self, contact_db):
        cache = MockDBCache({"contact/contact.db": contact_db})
        store = ContactStore(cache=cache, decrypted_dir="/nonexistent")
        assert store.resolve_username("Test") == "group1@chatroom"

    def test_resolve_username_not_found(self, contact_db):
        cache = MockDBCache({"contact/contact.db": contact_db})
        store = ContactStore(cache=cache, decrypted_dir="/nonexistent")
        assert store.resolve_username("nonexistent_person") is None

    def test_resolve_username_chatroom(self, contact_db):
        cache = MockDBCache({"contact/contact.db": contact_db})
        store = ContactStore(cache=cache, decrypted_dir="/nonexistent")
        assert store.resolve_username("group1@chatroom") == "group1@chatroom"

    def test_resolve_username_wxid_prefix(self, contact_db):
        cache = MockDBCache({"contact/contact.db": contact_db})
        store = ContactStore(cache=cache, decrypted_dir="/nonexistent")
        # wxid_ prefix is treated as direct username
        assert store.resolve_username("wxid_unknown") == "wxid_unknown"

    def test_display_name_for(self, contact_db):
        cache = MockDBCache({"contact/contact.db": contact_db})
        store = ContactStore(cache=cache, decrypted_dir="/nonexistent", db_dir="/some/wxid_me_1234/db")
        # Non-self user
        assert store.display_name_for("wxid_alice123") == "小爱"
        # Unknown user falls back to username
        assert store.display_name_for("wxid_unknown") == "wxid_unknown"
        # Empty username
        assert store.display_name_for("") == ""

    def test_no_contact_db(self):
        cache = MockDBCache()
        store = ContactStore(cache=cache, decrypted_dir="/nonexistent")
        assert store.get_names() == {}
        assert store.get_contacts() == []
        assert store.resolve_username("anyone") is None

    def test_get_group_members(self, contact_db):
        cache = MockDBCache({"contact/contact.db": contact_db})
        store = ContactStore(cache=cache, decrypted_dir="/nonexistent")
        result = store.get_group_members("group1@chatroom")
        assert result.owner == "小爱"
        assert result.member_count == 3

    def test_get_group_members_not_found(self, contact_db):
        cache = MockDBCache({"contact/contact.db": contact_db})
        store = ContactStore(cache=cache, decrypted_dir="/nonexistent")
        result = store.get_group_members("nonexistent@chatroom")
        assert result.members == []

    def test_get_contact_detail(self, contact_db):
        cache = MockDBCache({"contact/contact.db": contact_db})
        store = ContactStore(cache=cache, decrypted_dir="/nonexistent")
        detail = store.get_contact_detail("wxid_alice123")
        assert detail is not None
        assert detail.username == "wxid_alice123"
        assert detail.nick_name == "Alice"
        assert detail.remark == "小爱"

    def test_get_contact_detail_not_found(self, contact_db):
        cache = MockDBCache({"contact/contact.db": contact_db})
        store = ContactStore(cache=cache, decrypted_dir="/nonexistent")
        assert store.get_contact_detail("wxid_nonexistent") is None

    def test_pre_decrypted_dir_preferred(self, tmp_path, contact_db):
        """If a pre-decrypted contact.db exists, it's used over the cache."""
        import shutil
        pre_dec_dir = tmp_path / "decrypted" / "contact"
        pre_dec_dir.mkdir(parents=True)
        shutil.copy(contact_db, str(pre_dec_dir / "contact.db"))

        cache = MockDBCache()  # empty cache — would fail without pre-decrypted
        store = ContactStore(cache=cache, decrypted_dir=str(tmp_path / "decrypted"))
        names = store.get_names()
        assert "wxid_alice123" in names
