"""Tests for XML/app-message/media parsing in message_parser.

Covers the richer parsing paths not exercised by test_message_parser.py:
app messages (quote/file/link/mini-program), VoIP calls, media path
resolution, the XXE safety guard, and the message-text dispatcher.
"""

import os

import pytest

from wxq.services.message_parser import (
    _collapse_text,
    _parse_int,
    _parse_xml_root,
    format_app_message,
    format_message_text,
    format_voip_message,
    resolve_media_path,
)


def _noop_display(username, names):
    return names.get(username, username)


class TestParseXmlRoot:
    def test_valid_xml(self):
        root = _parse_xml_root("<root><a>1</a></root>")
        assert root is not None
        assert root.tag == "root"

    def test_empty_returns_none(self):
        assert _parse_xml_root("") is None

    def test_oversized_rejected(self):
        # > _XML_PARSE_MAX_LEN (20000)
        big = "<root>" + ("a" * 21000) + "</root>"
        assert _parse_xml_root(big) is None

    def test_doctype_rejected(self):
        """XXE guard — DOCTYPE declarations must be refused."""
        payload = '<!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><root>&xxe;</root>'
        assert _parse_xml_root(payload) is None

    def test_entity_rejected(self):
        payload = '<!ENTITY x "y"><root/>'
        assert _parse_xml_root(payload) is None

    def test_doctype_case_insensitive(self):
        assert _parse_xml_root("<!doctype html><root/>") is None

    def test_malformed_xml_returns_none(self):
        assert _parse_xml_root("<root><unclosed>") is None


class TestParseInt:
    def test_valid(self):
        assert _parse_int("42") == 42

    def test_none_fallback(self):
        assert _parse_int(None) == 0
        assert _parse_int(None, 7) == 7

    def test_invalid_fallback(self):
        assert _parse_int("abc", 3) == 3


class TestCollapseText:
    def test_whitespace_collapsed(self):
        assert _collapse_text("a   b\n\tc") == "a b c"

    def test_empty(self):
        assert _collapse_text("") == ""

    def test_strip(self):
        assert _collapse_text("  hi  ") == "hi"


def _appmsg(app_type: int, title: str = "T", extra: str = "") -> str:
    return f'<msg><appmsg><type>{app_type}</type><title>{title}</title>{extra}</appmsg></msg>'


class TestFormatAppMessage:
    def test_not_appmsg_returns_none(self):
        assert format_app_message("plain text", 49, False, "u", "d", {}, _noop_display) is None

    def test_link_type5(self):
        out = format_app_message(_appmsg(5, "Cool Article"), 49, False, "u", "d", {}, _noop_display)
        assert out == "[链接] Cool Article"

    def test_link_no_title(self):
        out = format_app_message(_appmsg(5, ""), 49, False, "u", "d", {}, _noop_display)
        assert out == "[链接]"

    def test_file_type6(self):
        out = format_app_message(_appmsg(6, "report.pdf"), 49, False, "u", "d", {}, _noop_display)
        assert out == "[文件] report.pdf"

    def test_miniprogram_type33(self):
        out = format_app_message(_appmsg(33, "MiniApp"), 49, False, "u", "d", {}, _noop_display)
        assert out == "[小程序] MiniApp"

    def test_miniprogram_type36(self):
        out = format_app_message(_appmsg(36, "MiniApp"), 49, False, "u", "d", {}, _noop_display)
        assert out == "[小程序] MiniApp"

    def test_quote_type57(self):
        extra = '<refermsg><displayname>Alice</displayname><content>original text</content></refermsg>'
        out = format_app_message(_appmsg(57, "my reply", extra), 49, False, "u", "d", {}, _noop_display)
        assert "my reply" in out
        assert "回复 Alice:" in out
        assert "original text" in out

    def test_quote_type57_no_refer(self):
        out = format_app_message(_appmsg(57, "just a reply"), 49, False, "u", "d", {}, _noop_display)
        assert "just a reply" in out

    def test_quote_long_content_truncated(self):
        long_ref = "x" * 300
        extra = f'<refermsg><displayname>Bob</displayname><content>{long_ref}</content></refermsg>'
        out = format_app_message(_appmsg(57, "reply", extra), 49, False, "u", "d", {}, _noop_display)
        assert "..." in out

    def test_unknown_apptype_with_title(self):
        out = format_app_message(_appmsg(999, "Something"), 49, False, "u", "d", {}, _noop_display)
        assert out == "[链接/文件] Something"

    def test_malformed_xml_returns_none(self):
        assert format_app_message("<appmsg><unclosed", 49, False, "u", "d", {}, _noop_display) is None


class TestFormatVoipMessage:
    def test_no_voip_tag(self):
        assert format_voip_message("nothing here") == "[通话]"

    def test_duration(self):
        xml = "<voip><msg>Duration: 05:30</msg></voip>"
        assert format_voip_message(xml) == "[通话] 通话时长 05:30"

    def test_canceled(self):
        xml = "<voip><msg>Canceled</msg></voip>"
        assert format_voip_message(xml) == "[通话] 已取消"

    def test_not_answered(self):
        xml = "<voip><msg>Call not answered</msg></voip>"
        assert format_voip_message(xml) == "[通话] 未接听"

    def test_busy(self):
        xml = "<voip><msg>Line busy</msg></voip>"
        assert format_voip_message(xml) == "[通话] 对方忙线"

    def test_unknown_status_passthrough(self):
        xml = "<voip><msg>Weird status</msg></voip>"
        assert format_voip_message(xml) == "[通话] Weird status"

    def test_empty_msg(self):
        xml = "<voip><msg></msg></voip>"
        assert format_voip_message(xml) == "[通话]"


class TestResolveMediaPath:
    def test_no_content(self):
        assert resolve_media_path("/db", None, 3, 0) == (None, False)

    def test_no_msg_dir(self, tmp_path):
        db_dir = str(tmp_path / "db")
        os.makedirs(db_dir)
        # No msg/ sibling dir
        assert resolve_media_path(db_dir, "content", 3, 0) == (None, False)

    def test_file_resolution(self, tmp_path):
        # Layout: base/db (db_dir), base/msg/file/YYYY-MM/report.pdf
        base = tmp_path / "wx"
        db_dir = base / "db"
        db_dir.mkdir(parents=True)
        from datetime import datetime
        ts = 1704067200  # 2024-01-01
        prefix = datetime.fromtimestamp(ts).strftime("%Y-%m")
        file_dir = base / "msg" / "file" / prefix
        file_dir.mkdir(parents=True)
        (file_dir / "report.pdf").write_text("data")

        content = '<msg><appmsg><type>6</type><title>report.pdf</title></appmsg></msg>'
        path, exists = resolve_media_path(str(db_dir), content, 49, ts)
        assert exists is True
        assert path.endswith("report.pdf")

    def test_file_not_found(self, tmp_path):
        base = tmp_path / "wx"
        db_dir = base / "db"
        db_dir.mkdir(parents=True)
        (base / "msg").mkdir()
        content = '<msg><appmsg><type>6</type><title>missing.pdf</title></appmsg></msg>'
        path, exists = resolve_media_path(str(db_dir), content, 49, 1704067200)
        assert exists is False


class TestFormatMessageText:
    def test_plain_text(self):
        sender, text = format_message_text(1, 1, "hello", False, "u", "d", {}, _noop_display)
        assert text == "hello"

    def test_image_no_media(self):
        sender, text = format_message_text(99, 3, None, False, "u", "d", {}, _noop_display)
        assert "[图片]" in text
        assert "local_id=99" in text

    def test_sticker_type47(self):
        sender, text = format_message_text(1, 47, "", False, "u", "d", {}, _noop_display)
        assert text == "[表情]"

    def test_voip_type50(self):
        xml = "<voip><msg>Canceled</msg></voip>"
        sender, text = format_message_text(1, 50, xml, False, "u", "d", {}, _noop_display)
        assert "[通话]" in text

    def test_app_type49_link(self):
        content = '<msg><appmsg><type>5</type><title>Link Here</title></appmsg></msg>'
        sender, text = format_message_text(1, 49, content, False, "u", "d", {}, _noop_display)
        assert "[链接] Link Here" == text

    def test_group_sender_extracted(self):
        sender, text = format_message_text(1, 1, "wxid_a:\nhi group", True, "g@chatroom", "Group", {}, _noop_display)
        assert sender == "wxid_a"
        assert text == "hi group"

    def test_other_type_labeled(self):
        # type 42 = 名片 (business card)
        sender, text = format_message_text(1, 42, "card", False, "u", "d", {}, _noop_display)
        assert text.startswith("[")
