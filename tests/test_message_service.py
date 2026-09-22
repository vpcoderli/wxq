"""Tests for the message service layer."""

import hashlib
import sqlite3
from contextlib import closing

import pytest

from tests.conftest import MockContactStore, MockDBCache
from wxq.exceptions import InvalidTimeRangeError, PaginationError
from wxq.models.message import ChatContext, MessageTableInfo
from wxq.services.message_service import (
    collect_chat_history,
    collect_chat_search,
    collect_chat_stats,
    find_msg_db_keys,
    parse_time_range,
    parse_time_value,
    resolve_chat_context,
    search_all_messages,
    validate_pagination,
)


class TestParseTimeValue:
    def test_full_datetime(self):
        ts = parse_time_value("2024-01-15 10:30:00", "start")
        assert ts is not None and ts > 0

    def test_datetime_no_seconds(self):
        ts = parse_time_value("2024-01-15 10:30", "start")
        assert ts is not None and ts > 0

    def test_date_only(self):
        ts = parse_time_value("2024-01-15", "start")
        assert ts is not None

    def test_date_end_sets_eod(self):
        ts_start = parse_time_value("2024-01-15", "start", is_end=False)
        ts_end = parse_time_value("2024-01-15", "end", is_end=True)
        assert ts_end > ts_start  # end should be 23:59:59

    def test_empty_returns_none(self):
        assert parse_time_value("", "start") is None
        assert parse_time_value("  ", "start") is None

    def test_invalid_format_raises(self):
        with pytest.raises(InvalidTimeRangeError):
            parse_time_value("not-a-date", "start")


class TestParseTimeRange:
    def test_both_empty(self):
        start, end = parse_time_range("", "")
        assert start is None and end is None

    def test_start_only(self):
        start, end = parse_time_range("2024-01-01", "")
        assert start is not None
        assert end is None

    def test_end_only(self):
        start, end = parse_time_range("", "2024-12-31")
        assert start is None
        assert end is not None

    def test_both_valid(self):
        start, end = parse_time_range("2024-01-01", "2024-12-31")
        assert start < end

    def test_inverted_range_raises(self):
        with pytest.raises(InvalidTimeRangeError):
            parse_time_range("2024-12-31", "2024-01-01")


class TestValidatePagination:
    def test_valid(self):
        validate_pagination(10, 0)  # should not raise

    def test_zero_limit_raises(self):
        with pytest.raises(PaginationError):
            validate_pagination(0, 0)

    def test_negative_limit_raises(self):
        with pytest.raises(PaginationError):
            validate_pagination(-1, 0)

    def test_negative_offset_raises(self):
        with pytest.raises(PaginationError):
            validate_pagination(10, -1)

    def test_limit_exceeds_max(self):
        with pytest.raises(PaginationError):
            validate_pagination(1000, 0, limit_max=500)

    def test_no_max(self):
        validate_pagination(99999, 0, limit_max=None)  # should not raise


class TestFindMsgDbKeys:
    def test_finds_message_keys(self):
        all_keys = {
            "message/message_0.db": {"enc_key": "aaa"},
            "message/message_1.db": {"enc_key": "bbb"},
            "contact/contact.db": {"enc_key": "ccc"},
            "session/session.db": {"enc_key": "ddd"},
        }
        result = find_msg_db_keys(all_keys)
        assert len(result) == 2
        assert "message/message_0.db" in result
        assert "message/message_1.db" in result

    def test_no_message_keys(self):
        all_keys = {"contact/contact.db": {"enc_key": "aaa"}}
        assert find_msg_db_keys(all_keys) == []


class TestResolveChatContext:
    def test_resolve_existing_contact(self, contact_db, message_db):
        db_path, table_name, username = message_db
        cache = MockDBCache({
            "contact/contact.db": contact_db,
            "message/message_0.db": db_path,
        })
        contacts = MockContactStore(contact_db)
        msg_db_keys = ["message/message_0.db"]

        ctx = resolve_chat_context("小爱", msg_db_keys, cache, contacts)
        assert ctx is not None
        assert ctx.username == "wxid_alice123"
        assert ctx.display_name == "小爱"
        assert ctx.is_group is False
        assert ctx.db_path is not None

    def test_resolve_not_found(self, contact_db):
        cache = MockDBCache({"contact/contact.db": contact_db})
        contacts = MockContactStore(contact_db)
        ctx = resolve_chat_context("Nonexistent", [], cache, contacts)
        assert ctx is None

    def test_resolve_group(self, contact_db, message_db):
        db_path, table_name, username = message_db
        cache = MockDBCache({
            "contact/contact.db": contact_db,
        })
        contacts = MockContactStore(contact_db)
        ctx = resolve_chat_context("Test Group", [], cache, contacts)
        assert ctx is not None
        assert ctx.is_group is True


class TestCollectChatHistory:
    def test_basic_history(self, message_db):
        db_path, table_name, username = message_db
        ctx = ChatContext(
            query="Alice", username=username, display_name="Alice",
            is_group=False,
            message_tables=[MessageTableInfo(db_path=db_path, table_name=table_name)],
        )
        names = {"wxid_alice123": "Alice", "wxid_me": "Me", "wxid_bob456": "Bob"}

        def display_fn(u, n):
            return n.get(u, u)

        lines, failures = collect_chat_history(ctx, names, display_fn, limit=10)
        assert len(lines) > 0
        assert len(failures) == 0
        # Messages should be in chronological order
        for line in lines:
            assert "[" in line  # has timestamp

    def test_history_pagination(self, message_db):
        db_path, table_name, username = message_db
        ctx = ChatContext(
            query="Alice", username=username, display_name="Alice",
            is_group=False,
            message_tables=[MessageTableInfo(db_path=db_path, table_name=table_name)],
        )
        names = {"wxid_alice123": "Alice", "wxid_me": "Me"}

        def display_fn(u, n):
            return n.get(u, u)

        lines_page1, _ = collect_chat_history(ctx, names, display_fn, limit=2, offset=0)
        lines_page2, _ = collect_chat_history(ctx, names, display_fn, limit=2, offset=2)
        assert len(lines_page1) == 2
        assert len(lines_page2) >= 1  # at least one more
        # No overlap
        assert set(lines_page1).isdisjoint(set(lines_page2))

    def test_history_empty_tables(self):
        ctx = ChatContext(
            query="Nobody", username="wxid_nobody", display_name="Nobody",
            is_group=False, message_tables=[],
        )
        lines, failures = collect_chat_history(ctx, {}, lambda u, n: u, limit=10)
        assert lines == []
        assert failures == []


class TestCollectChatSearch:
    def test_search_finds_keyword(self, message_db):
        db_path, table_name, username = message_db
        ctx = ChatContext(
            query="Alice", username=username, display_name="Alice",
            is_group=False,
            message_tables=[MessageTableInfo(db_path=db_path, table_name=table_name)],
        )
        names = {"wxid_alice123": "Alice", "wxid_me": "Me"}

        def display_fn(u, n):
            return n.get(u, u)

        entries, failures = collect_chat_search(
            ctx, names, "Hello", display_fn, candidate_limit=10,
        )
        assert len(entries) > 0
        # Each entry is (timestamp, text)
        for ts, text in entries:
            assert "Hello" in text

    def test_search_no_results(self, message_db):
        db_path, table_name, username = message_db
        ctx = ChatContext(
            query="Alice", username=username, display_name="Alice",
            is_group=False,
            message_tables=[MessageTableInfo(db_path=db_path, table_name=table_name)],
        )

        entries, failures = collect_chat_search(
            ctx, {}, "ZZZZNONEXISTENTZZZZ", lambda u, n: u, candidate_limit=10,
        )
        assert entries == []


class TestCollectChatStats:
    def test_basic_stats(self, message_db):
        db_path, table_name, username = message_db
        ctx = ChatContext(
            query="Alice", username=username, display_name="Alice",
            is_group=False,
            message_tables=[MessageTableInfo(db_path=db_path, table_name=table_name)],
        )
        names = {"wxid_alice123": "Alice", "wxid_me": "Me", "wxid_bob456": "Bob"}

        def display_fn(u, n):
            return n.get(u, u)

        stats = collect_chat_stats(ctx, names, display_fn)
        assert stats.total == 5
        assert "文本" in stats.type_breakdown
        assert stats.type_breakdown["文本"] == 3  # 3 text messages
        assert len(stats.top_senders) > 0

    def test_stats_with_time_range(self, message_db):
        db_path, table_name, username = message_db
        ctx = ChatContext(
            query="Alice", username=username, display_name="Alice",
            is_group=False,
            message_tables=[MessageTableInfo(db_path=db_path, table_name=table_name)],
        )

        # Very early timestamp — should find nothing
        stats = collect_chat_stats(
            ctx, {}, lambda u, n: u,
            start_ts=1000000000, end_ts=1000000001,
        )
        assert stats.total == 0


class TestSearchAllMessages:
    def test_global_search(self, message_db):
        db_path, table_name, username = message_db
        cache = MockDBCache({"message/message_0.db": db_path})
        names = {"wxid_alice123": "Alice", "wxid_me": "Me"}

        entries, failures = search_all_messages(
            ["message/message_0.db"], cache, names, "Hello",
            lambda u, n: n.get(u, u), candidate_limit=10,
        )
        assert len(entries) > 0
