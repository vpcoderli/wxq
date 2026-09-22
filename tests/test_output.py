"""Tests for the output formatter."""

import json
from io import StringIO

from wxq.output.formatter import output_json, output_text, output


class TestOutputJson:
    def test_dict_output(self, capsys):
        output_json({"key": "value"})
        captured = capsys.readouterr()
        parsed = json.loads(captured.out)
        assert parsed["key"] == "value"

    def test_list_output(self, capsys):
        output_json([1, 2, 3])
        captured = capsys.readouterr()
        parsed = json.loads(captured.out)
        assert parsed == [1, 2, 3]

    def test_unicode(self, capsys):
        output_json({"name": "小爱"})
        captured = capsys.readouterr()
        assert "小爱" in captured.out


class TestOutputText:
    def test_simple_text(self, capsys):
        output_text("Hello World")
        captured = capsys.readouterr()
        assert captured.out.strip() == "Hello World"


class TestOutputDispatch:
    def test_json_format(self, capsys):
        output({"a": 1}, "json")
        captured = capsys.readouterr()
        parsed = json.loads(captured.out)
        assert parsed["a"] == 1

    def test_text_format(self, capsys):
        output("plain text", "text")
        captured = capsys.readouterr()
        assert "plain text" in captured.out
