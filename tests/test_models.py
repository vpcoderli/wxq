"""Tests for domain models."""

from wxq.models.contact import Contact, ContactDetail, GroupMember, GroupInfo
from wxq.models.message import (
    MessageType,
    MSG_TYPE_FILTERS,
    Message,
    MessageTableInfo,
    ChatContext,
    ChatStats,
    HourlyDistribution,
    SenderStat,
)


class TestContact:
    def test_display_name_remark(self):
        c = Contact(username="wxid_123", nick_name="Nick", remark="Remark")
        assert c.display_name == "Remark"

    def test_display_name_nick(self):
        c = Contact(username="wxid_123", nick_name="Nick")
        assert c.display_name == "Nick"

    def test_display_name_fallback(self):
        c = Contact(username="wxid_123")
        assert c.display_name == "wxid_123"

    def test_is_group(self):
        assert Contact(username="abc@chatroom").is_group is True
        assert Contact(username="wxid_123").is_group is False

    def test_is_subscription(self):
        assert Contact(username="gh_official").is_subscription is True
        assert Contact(username="wxid_123").is_subscription is False


class TestContactDetail:
    def test_to_dict(self):
        cd = ContactDetail(
            username="wxid_123", nick_name="Nick", remark="Remark",
            alias="alias1", description="Desc"
        )
        d = cd.to_dict()
        assert d["username"] == "wxid_123"
        assert d["nick_name"] == "Nick"
        assert d["remark"] == "Remark"
        assert d["is_group"] is False
        assert d["is_subscription"] is False


class TestGroupInfo:
    def test_empty(self):
        gi = GroupInfo()
        assert gi.members == []
        assert gi.owner == ""
        assert gi.member_count == 0


class TestMessageType:
    def test_known_labels(self):
        assert MessageType.label(1) == "文本"
        assert MessageType.label(3) == "图片"
        assert MessageType.label(49) == "链接/文件"
        assert MessageType.label(10000) == "系统"

    def test_unknown_label(self):
        assert MessageType.label(9999) == "type=9999"

    def test_enum_values(self):
        assert MessageType.TEXT == 1
        assert MessageType.IMAGE == 3
        assert MessageType.APP_MSG == 49
        assert MessageType.VOIP == 50


class TestMsgTypeFilters:
    def test_filter_keys(self):
        expected_keys = {"text", "image", "voice", "video", "sticker",
                         "location", "link", "file", "call", "system"}
        assert set(MSG_TYPE_FILTERS.keys()) == expected_keys

    def test_file_has_subtype(self):
        assert MSG_TYPE_FILTERS["file"] == (49, 6)

    def test_text_no_subtype(self):
        assert MSG_TYPE_FILTERS["text"] == (1,)


class TestMessage:
    def test_format_line_with_sender(self):
        m = Message(local_id=1, local_type=1, create_time=1700000000,
                    sender_label="Alice", text="Hello")
        line = m.format_line()
        assert "Alice:" in line
        assert "Hello" in line

    def test_format_line_without_sender(self):
        m = Message(local_id=1, local_type=1, create_time=1700000000,
                    sender_label="", text="System message")
        line = m.format_line()
        assert "System message" in line
        assert ":" not in line.split("]")[1] or "System" in line


class TestChatContext:
    def test_db_path_with_tables(self):
        tables = [MessageTableInfo(db_path="/tmp/msg.db", table_name="Msg_abc")]
        ctx = ChatContext(query="Alice", username="wxid_123",
                          display_name="Alice", is_group=False,
                          message_tables=tables)
        assert ctx.db_path == "/tmp/msg.db"
        assert ctx.table_name == "Msg_abc"

    def test_db_path_empty(self):
        ctx = ChatContext(query="Alice", username="wxid_123",
                          display_name="Alice", is_group=False)
        assert ctx.db_path is None
        assert ctx.table_name is None

    def test_to_dict(self):
        ctx = ChatContext(query="Alice", username="wxid_123",
                          display_name="Alice", is_group=False)
        d = ctx.to_dict()
        assert d["username"] == "wxid_123"
        assert d["is_group"] is False


class TestChatStats:
    def test_to_dict(self):
        stats = ChatStats(
            total=100,
            type_breakdown={"文本": 80, "图片": 20},
            top_senders=[SenderStat(name="Alice", count=60)],
            hourly=HourlyDistribution(counts={9: 10, 14: 20}),
        )
        d = stats.to_dict()
        assert d["total"] == 100
        assert d["type_breakdown"]["文本"] == 80
        assert len(d["top_senders"]) == 1
        assert d["top_senders"][0]["name"] == "Alice"
        assert d["hourly"][9] == 10
        assert d["hourly"][0] == 0  # hour 0 default


class TestHourlyDistribution:
    def test_get_existing(self):
        h = HourlyDistribution(counts={10: 42})
        assert h.get(10) == 42

    def test_get_missing(self):
        h = HourlyDistribution()
        assert h.get(5) == 0

    def test_to_dict_has_24_hours(self):
        h = HourlyDistribution(counts={12: 5})
        d = h.to_dict()
        assert len(d) == 24
        assert d[12] == 5
        assert d[0] == 0
