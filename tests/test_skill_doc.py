"""Keep SKILL.md honest about the real CLI surface.

SKILL.md tells an AI agent which commands and flags exist. A stale entry there
makes the agent emit commands that do not work, so these tests diff the document
against Click's own registry rather than trusting the prose. The README once
documented a `contact-detail` command that never existed; this is the guard that
would have caught it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from wxq.cli import cli
from wxq.models.message import MSG_TYPE_FILTERS

SKILL_PATH = Path(__file__).resolve().parent.parent / "SKILL.md"

# Absent from an installed sdist; only the repo checkout carries it.
pytestmark = pytest.mark.skipif(
    not SKILL_PATH.exists(), reason="SKILL.md is only present in the repo checkout"
)


def _split_frontmatter() -> tuple[dict[str, object], str]:
    text = SKILL_PATH.read_text(encoding="utf-8")
    match = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    assert match, "SKILL.md must open with a YAML frontmatter block"
    return yaml.safe_load(match.group(1)), text[match.end() :]


def _documented_rows(body: str) -> list[tuple[str, str]]:
    """Command-table rows as (command, options cell)."""
    return re.findall(r"^\| `([a-z-]+)[^`]*` \| ([^|]+)\|", body, re.M)


def _real_options(command_name: str) -> set[str]:
    command = cli.commands[command_name]
    return {
        opt
        for param in command.params
        for opt in param.opts
        if opt.startswith("--")
    } - {"--help"}


class TestFrontmatter:
    def test_parses_and_has_required_keys(self):
        front, _ = _split_frontmatter()
        assert front["name"], "a skill needs a name"
        assert front["description"], "a skill needs a description"

    def test_name_is_a_valid_slug(self):
        front, _ = _split_frontmatter()
        assert re.fullmatch(r"[a-z0-9-]+", str(front["name"]))

    def test_name_matches_the_console_script(self):
        front, _ = _split_frontmatter()
        assert front["name"] == "wxq"


class TestCommandTable:
    def test_documents_every_command_and_no_others(self):
        _, body = _split_frontmatter()
        documented = {name for name, _ in _documented_rows(body)}
        assert documented == set(cli.commands)

    @pytest.mark.parametrize("command_name", sorted(cli.commands))
    def test_options_match_source(self, command_name):
        _, body = _split_frontmatter()
        cell = dict(_documented_rows(body))[command_name]
        documented = set(re.findall(r"--[a-z-]+", cell))
        assert documented == _real_options(command_name)


class TestTypeVocabularies:
    """`--type` means different things on messages and on favorites."""

    def test_message_types(self):
        _, body = _split_frontmatter()
        match = re.search(r"`search` is one of:\n  `([^`]+)`", body)
        assert match, "the message --type list is missing from SKILL.md"
        assert set(match.group(1).split()) == set(MSG_TYPE_FILTERS)

    def test_favorite_types(self):
        _, body = _split_frontmatter()
        match = re.search(r"`favorites --type`[^`]*`([a-z ]+)`", body)
        assert match, "the favorites --type list is missing from SKILL.md"
        param = next(p for p in cli.commands["favorites"].params if "--type" in p.opts)
        assert set(match.group(1).split()) == set(param.type.choices)


class TestDocumentedAbsences:
    def test_contact_detail_is_still_absent(self):
        """SKILL.md warns that this command does not exist. Keep that true."""
        assert "contact-detail" not in cli.commands
