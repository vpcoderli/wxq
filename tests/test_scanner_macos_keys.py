"""Regression test: macOS scanner output has no salt field.

The bundled C scanner writes {"rel/path.db": {"enc_key": "..."}}. The Python
wrapper must still count those entries as extracted keys.
"""

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

from wxq.keys import scanner_macos


def test_extract_keys_counts_entries_without_salt(tmp_path: Path, monkeypatch):
    account_dir = tmp_path / "account"
    db_dir = account_dir / "db_storage"
    db_dir.mkdir(parents=True)
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    c_output = account_dir / "all_keys.json"
    c_output.write_text(
        json.dumps(
            {
                "message/message_0.db": {"enc_key": "aa" * 32},
                "contact/contact.db": {"enc_key": "bb" * 32},
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(scanner_macos, "_find_binary", lambda: "/fake/find_all_keys")
    monkeypatch.setattr(
        scanner_macos.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(stdout="", stderr="", returncode=0),
    )

    output_path = state_dir / "all_keys.json"
    key_map = scanner_macos.extract_keys(str(db_dir), str(output_path), print_fn=lambda _m: None)

    assert len(key_map) == 2
    saved = json.loads(output_path.read_text(encoding="utf-8"))
    assert set(saved) == {"message/message_0.db", "contact/contact.db"}
    assert not c_output.exists()
