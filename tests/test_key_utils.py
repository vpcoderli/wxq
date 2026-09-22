"""Tests for key utility functions."""

import os

from wxq.core.key_utils import (
    strip_key_metadata,
    key_path_variants,
    get_key_info,
)


class TestStripKeyMetadata:
    def test_keeps_entries_with_enc_key(self):
        raw = {
            "message/message_0.db": {"enc_key": "abc123", "salt": "def"},
            "no_key_entry": {"salt": "xyz"},
            "contact/contact.db": {"enc_key": "qqq", "extra": 42},
        }
        result = strip_key_metadata(raw)
        assert "message/message_0.db" in result
        assert "contact/contact.db" in result
        assert "no_key_entry" not in result

    def test_empty_dict(self):
        assert strip_key_metadata({}) == {}

    def test_non_dict_values_skipped(self):
        raw = {"key1": "not a dict", "key2": {"enc_key": "valid"}}
        result = strip_key_metadata(raw)
        assert "key1" not in result
        assert "key2" in result


class TestKeyPathVariants:
    def test_forward_slash(self):
        variants = key_path_variants("message/message_0.db")
        assert "message/message_0.db" in variants

    def test_backslash_generates_forward(self):
        variants = key_path_variants("message\\message_0.db")
        assert "message/message_0.db" in variants

    def test_simple_name(self):
        variants = key_path_variants("contact.db")
        assert "contact.db" in variants


class TestGetKeyInfo:
    def test_direct_match(self):
        keys = {"message/msg.db": {"enc_key": "abc"}}
        result = get_key_info(keys, "message/msg.db")
        assert result is not None
        assert result["enc_key"] == "abc"

    def test_cross_platform_match(self):
        keys = {"message\\msg.db": {"enc_key": "abc"}}
        result = get_key_info(keys, "message/msg.db")
        assert result is not None

    def test_no_match(self):
        keys = {"message/msg.db": {"enc_key": "abc"}}
        assert get_key_info(keys, "contact/contact.db") is None

    def test_path_traversal_rejected(self):
        keys = {"../../etc/passwd": {"enc_key": "evil"}}
        assert get_key_info(keys, "../../etc/passwd") is None

    def test_absolute_path_rejected(self):
        keys = {"/etc/passwd": {"enc_key": "evil"}}
        assert get_key_info(keys, "/etc/passwd") is None
