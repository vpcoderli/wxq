"""Tests for the MCP server module."""

import json
from unittest.mock import MagicMock, patch

import pytest

from wxq.mcp.server import (
    TOOLS,
    _json_response,
    _error_response,
    _handle_contacts,
    _handle_sessions,
    _handle_unread,
    _handle_chat_history,
    _handle_search,
    _handle_stats,
    _handle_members,
    _handle_contact_detail,
)


class TestToolDefinitions:
    def test_tool_count(self):
        assert len(TOOLS) == 8

    def test_tool_names(self):
        names = {t.name for t in TOOLS}
        expected = {
            "get_sessions", "get_unread", "get_contacts",
            "get_contact_detail", "get_chat_history", "search_messages",
            "get_chat_stats", "get_group_members",
        }
        assert names == expected

    def test_all_tools_have_input_schema(self):
        for tool in TOOLS:
            assert "type" in tool.inputSchema
            assert tool.inputSchema["type"] == "object"

    def test_required_params(self):
        tool_map = {t.name: t for t in TOOLS}
        assert "chat_name" in tool_map["get_chat_history"].inputSchema.get("required", [])
        assert "keyword" in tool_map["search_messages"].inputSchema.get("required", [])
        assert "name" in tool_map["get_contact_detail"].inputSchema.get("required", [])
        assert "group_name" in tool_map["get_group_members"].inputSchema.get("required", [])
        assert "chat_name" in tool_map["get_chat_stats"].inputSchema.get("required", [])


class TestResponseHelpers:
    def test_json_response(self):
        result = _json_response({"key": "value"})
        assert len(result) == 1
        assert result[0].type == "text"
        parsed = json.loads(result[0].text)
        assert parsed["key"] == "value"

    def test_json_response_unicode(self):
        result = _json_response({"name": "小爱"})
        assert "小爱" in result[0].text

    def test_error_response(self):
        result = _error_response("something went wrong")
        assert len(result) == 1
        parsed = json.loads(result[0].text)
        assert "error" in parsed
        assert parsed["error"] == "something went wrong"


class TestHandleContacts:
    def test_list_all(self, contact_db):
        from tests.conftest import MockDBCache
        from wxq.core.contacts import ContactStore

        cache = MockDBCache({"contact/contact.db": contact_db})
        app = MagicMock()
        app.contacts = ContactStore(cache=cache, decrypted_dir="/nonexistent")

        result = _handle_contacts(app, {"limit": 50})
        parsed = json.loads(result[0].text)
        assert len(parsed) > 0

    def test_search_query(self, contact_db):
        from tests.conftest import MockDBCache
        from wxq.core.contacts import ContactStore

        cache = MockDBCache({"contact/contact.db": contact_db})
        app = MagicMock()
        app.contacts = ContactStore(cache=cache, decrypted_dir="/nonexistent")

        result = _handle_contacts(app, {"query": "alice", "limit": 50})
        parsed = json.loads(result[0].text)
        assert len(parsed) >= 1


class TestHandleSessions:
    def test_list_sessions(self, session_db, contact_db):
        from tests.conftest import MockDBCache
        from wxq.core.contacts import ContactStore

        cache = MockDBCache({
            "session/session.db": session_db,
            "contact/contact.db": contact_db,
        })
        app = MagicMock()
        app.cache = cache
        app.contacts = ContactStore(cache=cache, decrypted_dir="/nonexistent")

        result = _handle_sessions(app, {"limit": 20})
        parsed = json.loads(result[0].text)
        assert len(parsed) == 3
        # Should be sorted by timestamp descending
        assert parsed[0]["timestamp"] >= parsed[1]["timestamp"]

    def test_session_db_missing(self):
        from tests.conftest import MockDBCache
        app = MagicMock()
        app.cache = MockDBCache()
        result = _handle_sessions(app, {})
        parsed = json.loads(result[0].text)
        assert "error" in parsed


class TestHandleUnread:
    def test_list_unread(self, session_db, contact_db):
        from tests.conftest import MockDBCache
        from wxq.core.contacts import ContactStore

        cache = MockDBCache({
            "session/session.db": session_db,
            "contact/contact.db": contact_db,
        })
        app = MagicMock()
        app.cache = cache
        app.contacts = ContactStore(cache=cache, decrypted_dir="/nonexistent")

        result = _handle_unread(app, {"limit": 50})
        parsed = json.loads(result[0].text)
        assert len(parsed) == 2  # only Alice (3 unread) and group (5 unread)
        for item in parsed:
            assert item["unread"] > 0


class TestHandleMembers:
    def test_get_members(self, contact_db):
        from tests.conftest import MockDBCache
        from wxq.core.contacts import ContactStore

        cache = MockDBCache({"contact/contact.db": contact_db})
        app = MagicMock()
        app.contacts = ContactStore(cache=cache, decrypted_dir="/nonexistent")

        result = _handle_members(app, {"group_name": "Test Group"})
        parsed = json.loads(result[0].text)
        assert "members" in parsed
        assert parsed["member_count"] == 3

    def test_not_a_group(self, contact_db):
        from tests.conftest import MockDBCache
        from wxq.core.contacts import ContactStore

        cache = MockDBCache({"contact/contact.db": contact_db})
        app = MagicMock()
        app.contacts = ContactStore(cache=cache, decrypted_dir="/nonexistent")

        result = _handle_members(app, {"group_name": "Alice"})
        parsed = json.loads(result[0].text)
        assert "error" in parsed

    def test_group_not_found(self, contact_db):
        from tests.conftest import MockDBCache
        from wxq.core.contacts import ContactStore

        cache = MockDBCache({"contact/contact.db": contact_db})
        app = MagicMock()
        app.contacts = ContactStore(cache=cache, decrypted_dir="/nonexistent")

        result = _handle_members(app, {"group_name": "nonexistent_group"})
        parsed = json.loads(result[0].text)
        assert "error" in parsed
