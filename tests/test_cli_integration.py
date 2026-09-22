"""CLI integration smoke tests.

Each CLI command is invoked directly through Click's CliRunner with a
fake AppContext assembled from real fixtures, exercising the full
command -> service -> parser path without needing key extraction or
live decryption. Proves the commands wire up and don't crash on the
dataclass/dict boundaries that unit tests alone missed.
"""

from types import SimpleNamespace

import json
import pytest
from click.testing import CliRunner

from tests.conftest import MockDBCache
from wxq.core.contacts import ContactStore
from wxq.commands.sessions import sessions
from wxq.commands.unread import unread
from wxq.commands.contacts import contacts as contacts_cmd
from wxq.commands.history import history
from wxq.commands.search import search
from wxq.commands.stats import stats
from wxq.commands.members import members
from wxq.commands.export import export


@pytest.fixture()
def app(contact_db, message_db, session_db):
    """A fake AppContext backed by real fixture DBs."""
    db_path, table_name, username = message_db
    cache = MockDBCache({
        "contact/contact.db": contact_db,
        "session/session.db": session_db,
        "message/message_0.db": db_path,
    })
    store = ContactStore(cache=cache, decrypted_dir="/nonexistent")

    def display_name_fn(u, names):
        return store.display_name_for(u)

    return SimpleNamespace(
        cache=cache,
        contacts=store,
        msg_db_keys=["message/message_0.db"],
        display_name_fn=display_name_fn,
        decrypted_dir="/nonexistent",
        db_dir="/nonexistent",
    )


def _run(cmd, args, app):
    return CliRunner().invoke(cmd, args, obj=app)


class TestSessionsCommand:
    def test_json(self, app):
        r = _run(sessions, ["--format", "json"], app)
        assert r.exit_code == 0, r.output
        data = json.loads(r.output)
        assert len(data) == 3

    def test_text(self, app):
        r = _run(sessions, ["--format", "text"], app)
        assert r.exit_code == 0, r.output


class TestUnreadCommand:
    def test_json(self, app):
        r = _run(unread, ["--format", "json"], app)
        assert r.exit_code == 0, r.output
        data = json.loads(r.output)
        # 2 sessions have unread > 0
        assert len(data) == 2


class TestContactsCommand:
    def test_list(self, app):
        r = _run(contacts_cmd, ["--format", "json"], app)
        assert r.exit_code == 0, r.output
        data = json.loads(r.output)
        assert len(data) >= 1

    def test_query(self, app):
        r = _run(contacts_cmd, ["--query", "Alice", "--format", "json"], app)
        assert r.exit_code == 0, r.output


class TestHistoryCommand:
    def test_json(self, app):
        r = _run(history, ["小爱", "--format", "json"], app)
        assert r.exit_code == 0, r.output

    def test_not_found(self, app):
        r = _run(history, ["NoSuchPerson", "--format", "json"], app)
        assert r.exit_code == 1


class TestSearchCommand:
    def test_global(self, app):
        r = _run(search, ["Hello", "--format", "json"], app)
        assert r.exit_code == 0, r.output

    def test_scoped(self, app):
        r = _run(search, ["Hello", "--chat", "小爱", "--format", "json"], app)
        assert r.exit_code == 0, r.output


class TestStatsCommand:
    """Directly exercises the dataclass->text path that mypy flagged."""

    def test_json(self, app):
        r = _run(stats, ["小爱", "--format", "json"], app)
        assert r.exit_code == 0, r.output
        data = json.loads(r.output)
        assert data["total"] == 5

    def test_text(self, app):
        r = _run(stats, ["小爱", "--format", "text"], app)
        assert r.exit_code == 0, r.output
        # The text renderer walks type_breakdown, top_senders, hourly
        assert "Total messages: 5" in r.output
        assert "24-hour activity" in r.output


class TestMembersCommand:
    def test_group(self, app):
        r = _run(members, ["Test Group", "--format", "json"], app)
        assert r.exit_code == 0, r.output
        data = json.loads(r.output)
        assert data["member_count"] == 3

    def test_not_a_group(self, app):
        r = _run(members, ["小爱", "--format", "json"], app)
        assert r.exit_code == 1


class TestExportCommand:
    def test_markdown_stdout(self, app):
        r = _run(export, ["小爱", "--format", "markdown"], app)
        assert r.exit_code == 0, r.output
        assert "小爱" in r.output

    def test_txt_to_file(self, app, tmp_path):
        out = str(tmp_path / "chat.txt")
        r = _run(export, ["小爱", "--format", "txt", "--output", out], app)
        assert r.exit_code == 0, r.output
        with open(out, encoding="utf-8") as f:
            assert len(f.read()) > 0

    def test_not_found(self, app):
        r = _run(export, ["NoSuchPerson"], app)
        assert r.exit_code == 1
