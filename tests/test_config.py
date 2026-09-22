"""Tests for configuration loading."""

import json
import os

import pytest

from wxq.core.config import load_config
from wxq.exceptions import ConfigError, DataDirNotFoundError


def _write_config(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)


class TestLoadConfig:
    def test_explicit_db_dir(self, tmp_path):
        cfg_path = str(tmp_path / "config.json")
        db_dir = str(tmp_path / "db_storage")
        os.makedirs(db_dir)
        _write_config(cfg_path, {"db_dir": db_dir})

        cfg = load_config(cfg_path)
        assert cfg.db_dir == db_dir

    def test_derives_defaults(self, tmp_path):
        cfg_path = str(tmp_path / "config.json")
        db_dir = str(tmp_path / "db_storage")
        os.makedirs(db_dir)
        _write_config(cfg_path, {"db_dir": db_dir})

        cfg = load_config(cfg_path)
        # keys_file and decrypted_dir default alongside the config file
        assert cfg.keys_file.endswith("all_keys.json")
        assert cfg.decrypted_dir.endswith("decrypted")

    def test_wechat_base_dir_from_db_storage(self, tmp_path):
        cfg_path = str(tmp_path / "config.json")
        db_dir = str(tmp_path / "wxdata" / "db_storage")
        os.makedirs(db_dir)
        _write_config(cfg_path, {"db_dir": db_dir})

        cfg = load_config(cfg_path)
        # basename is db_storage → base dir is its parent
        assert cfg.wechat_base_dir == os.path.dirname(db_dir)

    def test_wechat_base_dir_non_db_storage(self, tmp_path):
        cfg_path = str(tmp_path / "config.json")
        db_dir = str(tmp_path / "customdir")
        os.makedirs(db_dir)
        _write_config(cfg_path, {"db_dir": db_dir})

        cfg = load_config(cfg_path)
        assert cfg.wechat_base_dir == db_dir

    def test_relative_paths_made_absolute(self, tmp_path):
        cfg_path = str(tmp_path / "config.json")
        db_dir = str(tmp_path / "db_storage")
        os.makedirs(db_dir)
        _write_config(cfg_path, {"db_dir": db_dir, "keys_file": "relative_keys.json"})

        cfg = load_config(cfg_path)
        assert os.path.isabs(cfg.keys_file)

    def test_malformed_json_raises(self, tmp_path):
        cfg_path = str(tmp_path / "config.json")
        with open(cfg_path, "w", encoding="utf-8") as f:
            f.write("{ this is not valid json ")

        with pytest.raises(ConfigError):
            load_config(cfg_path)

    def test_missing_db_dir_no_autodetect_raises(self, tmp_path, monkeypatch):
        cfg_path = str(tmp_path / "config.json")
        _write_config(cfg_path, {})  # no db_dir
        # Force auto-detection to find nothing
        monkeypatch.setattr(
            "wxq.core.config.auto_detect_db_dir", lambda: None
        )
        with pytest.raises(DataDirNotFoundError):
            load_config(cfg_path)

    def test_autodetect_used_when_db_dir_missing(self, tmp_path, monkeypatch):
        cfg_path = str(tmp_path / "config.json")
        detected = str(tmp_path / "auto" / "db_storage")
        os.makedirs(detected)
        _write_config(cfg_path, {})
        monkeypatch.setattr(
            "wxq.core.config.auto_detect_db_dir", lambda: detected
        )
        cfg = load_config(cfg_path)
        assert cfg.db_dir == detected

    def test_nonexistent_config_falls_back_to_autodetect(self, tmp_path, monkeypatch):
        cfg_path = str(tmp_path / "does_not_exist.json")
        detected = str(tmp_path / "auto" / "db_storage")
        os.makedirs(detected)
        monkeypatch.setattr(
            "wxq.core.config.auto_detect_db_dir", lambda: detected
        )
        cfg = load_config(cfg_path)
        assert cfg.db_dir == detected
