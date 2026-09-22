"""Additional CLI coverage: favorites, contacts --detail, new-messages."""

import json
import os
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from tests.conftest import MockDBCache
from wxq.core.contacts import ContactStore
from wxq.commands import new_messages as nm_module
from wxq.commands.favorites import favorites, _parse_fav_content
from wxq.commands.contacts import contacts as contacts_cmd
from wxq.commands.new_messages import new_messages


def _run(cmd, args, app):
    return CliRunner().invoke(cmd, args, obj=app)


@pytest.fixture()
def app(contact_db, favorite_db, session_db):
    cache = MockDBCache({
        "contact/contact.db": contact_db,
        "favorite/favorite.db": favorite_db,
        "session/session.db": session_db,
    })
    store = ContactStore(cache=cache, decrypted_dir="/nonexistent")
    return SimpleNamespace(
        cache=cache,
        contacts=store,
        decrypted_dir="/nonexistent",
        db_dir="/nonexistent",
        display_name_fn=lambda u, names: store.display_name_for(u),
    )


class TestParseFavContent:
    def test_text_type(self):
        xml = '<favitem><desc>a note</desc></favitem>'
        assert _parse_fav_content(xml, 1) == "a note"

    def test_image_type(self):
        assert _parse_fav_content('<favitem></favitem>', 2) == "[Image]"

    def test_article_type(self):
        xml = '<favitem><pagetitle>Title</pagetitle><pagedesc>Desc</pagedesc></favitem>'
        assert _parse_fav_content(xml, 5) == "Title - Desc"

    def test_none_content(self):
        assert _parse_fav_content(None, 1) == ""

    def test_malformed_xml(self):
        assert _parse_fav_content("<broken", 1) == ""

    def test_unknown_type_fallback(self):
        assert _parse_fav_content('<favitem><desc>x</desc></favitem>', 999) == "x"


class TestFavoritesCommand:
    def test_list_json(self, app):
        r = _run(favorites, ["--format", "json"], app)
        assert r.exit_code == 0, r.output
        data = json.loads(r.output)
        assert data["count"] == 2

    def test_list_text(self, app):
        r = _run(favorites, ["--format", "text"], app)
        assert r.exit_code == 0, r.output

    def test_type_filter(self, app):
        r = _run(favorites, ["--type", "article", "--format", "json"], app)
        assert r.exit_code == 0, r.output
        data = json.loads(r.output)
        assert data["count"] == 1

    def test_query_filter(self, app):
        r = _run(favorites, ["--query", "Article", "--format", "json"], app)
        assert r.exit_code == 0, r.output

    def test_missing_db(self, contact_db):
        cache = MockDBCache({"contact/contact.db": contact_db})  # no favorite.db
        store = ContactStore(cache=cache, decrypted_dir="/nonexistent")
        app = SimpleNamespace(cache=cache, contacts=store, decrypted_dir="/nonexistent")
        r = _run(favorites, [], app)
        assert r.exit_code == 3


class TestContactsDetail:
    def test_detail_json(self, app):
        r = _run(contacts_cmd, ["--detail", "小爱", "--format", "json"], app)
        assert r.exit_code == 0, r.output
        data = json.loads(r.output)
        assert data["username"] == "wxid_alice123"

    def test_detail_text(self, app):
        r = _run(contacts_cmd, ["--detail", "小爱", "--format", "text"], app)
        assert r.exit_code == 0, r.output

    def test_detail_not_found(self, app):
        r = _run(contacts_cmd, ["--detail", "wxid_ghost"], app)
        # _show_detail echoes an error but returns normally
        assert "not found" in r.output.lower()


class TestNewMessagesCommand:
    def test_first_call_then_diff(self, app, tmp_path, monkeypatch):
        state_file = str(tmp_path / "last_check.json")
        monkeypatch.setattr(nm_module, "STATE_FILE", state_file)
        monkeypatch.setattr(nm_module, "STATE_DIR", str(tmp_path))

        # First call: no prior state -> first_call True, saves state
        r1 = _run(new_messages, ["--format", "json"], app)
        assert r1.exit_code == 0, r1.output
        d1 = json.loads(r1.output)
        assert d1["first_call"] is True
        assert os.path.exists(state_file)

        # Second call: state matches -> not first call, no new messages
        r2 = _run(new_messages, ["--format", "json"], app)
        assert r2.exit_code == 0, r2.output
        d2 = json.loads(r2.output)
        assert d2.get("first_call") is not True
