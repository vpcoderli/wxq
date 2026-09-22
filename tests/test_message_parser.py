"""Tests for message content parsing."""

import zstandard as zstd

from wxq.services.message_parser import (
    decompress_content,
    format_msg_type,
    split_msg_type,
    parse_message_content,
)


class TestDecompressContent:
    def test_string_passthrough(self):
        assert decompress_content("hello", None) == "hello"

    def test_none_passthrough(self):
        assert decompress_content(None, None) is None

    def test_bytes_decode(self):
        assert decompress_content("hello".encode(), None) == "hello"

    def test_zstd_decompress(self):
        cctx = zstd.ZstdCompressor()
        compressed = cctx.compress(b"Hello WeChat")
        result = decompress_content(compressed, 4)
        assert result == "Hello WeChat"

    def test_zstd_invalid_data(self):
        result = decompress_content(b"\x00\x01\x02\x03", 4)
        assert result is None

    def test_non_zstd_bytes(self):
        result = decompress_content(b"plain bytes", 0)
        assert result == "plain bytes"

    def test_bytes_with_none_ct(self):
        result = decompress_content(b"plain bytes", None)
        assert result == "plain bytes"


class TestSplitMsgType:
    def test_simple_type(self):
        assert split_msg_type(1) == (1, 0)
        assert split_msg_type(49) == (49, 0)

    def test_combined_type(self):
        # file subtype 6 combined with app_msg type 49
        combined = 49 | (6 << 32)
        base, sub = split_msg_type(combined)
        assert base == 49
        assert sub == 6

    def test_zero(self):
        assert split_msg_type(0) == (0, 0)

    def test_invalid_type(self):
        assert split_msg_type(None) == (0, 0)
        assert split_msg_type("bad") == (0, 0)


class TestFormatMsgType:
    def test_known_types(self):
        assert format_msg_type(1) == "文本"
        assert format_msg_type(3) == "图片"
        assert format_msg_type(49) == "链接/文件"
        assert format_msg_type(10000) == "系统"

    def test_unknown_type(self):
        assert format_msg_type(9999) == "type=9999"

    def test_combined_type(self):
        # Combined type should use only the base type for labeling
        combined = 49 | (6 << 32)
        assert format_msg_type(combined) == "链接/文件"


class TestParseMessageContent:
    def test_simple_text(self):
        sender, text = parse_message_content("Hello!", 1, False)
        assert sender == ""
        assert text == "Hello!"

    def test_group_message(self):
        sender, text = parse_message_content("wxid_alice:\nHello group!", 1, True)
        assert sender == "wxid_alice"
        assert text == "Hello group!"

    def test_group_no_separator(self):
        sender, text = parse_message_content("Just text", 1, True)
        assert sender == ""
        assert text == "Just text"

    def test_none_content(self):
        sender, text = parse_message_content(None, 1, False)
        assert sender == ""
        assert text == ""

    def test_bytes_content(self):
        sender, text = parse_message_content(b"\x00\x01", 3, False)
        assert sender == ""
        assert text == "(二进制内容)"

    def test_non_group_with_colon_newline(self):
        """In non-group chat, colon+newline is NOT a sender prefix."""
        sender, text = parse_message_content("key:\nvalue", 1, False)
        assert sender == ""
        assert text == "key:\nvalue"
