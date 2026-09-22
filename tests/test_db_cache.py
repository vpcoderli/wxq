"""Tests for DBCache — decryption caching with mtime invalidation."""

import os
import shutil

import pytest

from wxq.core.db_cache import DBCache


@pytest.fixture()
def isolated_cache(tmp_path, monkeypatch):
    """Point DBCache's class-level cache dir at an isolated temp location."""
    cache_dir = str(tmp_path / "cache")
    os.makedirs(cache_dir, exist_ok=True)
    monkeypatch.setattr(DBCache, "CACHE_DIR", cache_dir)
    monkeypatch.setattr(DBCache, "MTIME_FILE", os.path.join(cache_dir, "_mtimes.json"))
    return cache_dir


@pytest.fixture()
def encrypted_db_dir(tmp_path, fake_encrypted_db):
    """Lay out an encrypted contact.db under a db_dir tree.

    Returns (db_dir, all_keys).
    """
    db_path, enc_key_hex, _ = fake_encrypted_db
    db_dir = tmp_path / "db_storage"
    (db_dir / "contact").mkdir(parents=True)
    dest = db_dir / "contact" / "contact.db"
    shutil.copy(db_path, str(dest))
    all_keys = {"contact/contact.db": {"enc_key": enc_key_hex}}
    return str(db_dir), all_keys


class TestDBCacheGet:
    def test_unknown_key_returns_none(self, isolated_cache, encrypted_db_dir):
        db_dir, all_keys = encrypted_db_dir
        cache = DBCache(all_keys, db_dir)
        assert cache.get("session/session.db") is None

    def test_missing_source_file_returns_none(self, isolated_cache, tmp_path):
        all_keys = {"contact/contact.db": {"enc_key": "aa" * 32}}
        cache = DBCache(all_keys, str(tmp_path / "nonexistent"))
        assert cache.get("contact/contact.db") is None

    def test_decrypts_on_miss(self, isolated_cache, encrypted_db_dir):
        db_dir, all_keys = encrypted_db_dir
        cache = DBCache(all_keys, db_dir)
        path = cache.get("contact/contact.db")
        assert path is not None
        assert os.path.exists(path)
        # Decrypted output should begin with the SQLite magic header
        with open(path, "rb") as f:
            assert f.read(16).startswith(b"SQLite format 3")

    def test_cache_hit_returns_same_path(self, isolated_cache, encrypted_db_dir):
        db_dir, all_keys = encrypted_db_dir
        cache = DBCache(all_keys, db_dir)
        p1 = cache.get("contact/contact.db")
        mtime1 = os.path.getmtime(p1)
        p2 = cache.get("contact/contact.db")
        assert p1 == p2
        # Not re-decrypted — output file untouched
        assert os.path.getmtime(p2) == mtime1

    def test_mtime_change_invalidates(self, isolated_cache, encrypted_db_dir):
        db_dir, all_keys = encrypted_db_dir
        cache = DBCache(all_keys, db_dir)
        p1 = cache.get("contact/contact.db")
        decrypted_mtime1 = os.path.getmtime(p1)

        # Bump the source mtime forward — should trigger re-decrypt
        src = os.path.join(db_dir, "contact", "contact.db")
        future = os.path.getmtime(src) + 100
        os.utime(src, (future, future))

        p2 = cache.get("contact/contact.db")
        assert p2 is not None
        assert os.path.getmtime(p2) >= decrypted_mtime1

    def test_backslash_key_normalized(self, isolated_cache, encrypted_db_dir):
        db_dir, all_keys = encrypted_db_dir
        cache = DBCache(all_keys, db_dir)
        # Windows-style separator should resolve to the same DB
        path = cache.get("contact\\contact.db")
        assert path is not None
        assert os.path.exists(path)


class TestDBCachePersistence:
    def test_persistent_cache_survives_new_instance(self, isolated_cache, encrypted_db_dir):
        db_dir, all_keys = encrypted_db_dir
        cache1 = DBCache(all_keys, db_dir)
        p1 = cache1.get("contact/contact.db")
        cache1.cleanup()

        # A fresh instance should reload the persisted mtime map and reuse the file
        cache2 = DBCache(all_keys, db_dir)
        p2 = cache2.get("contact/contact.db")
        assert p1 == p2

    def test_cleanup_writes_mtime_file(self, isolated_cache, encrypted_db_dir):
        db_dir, all_keys = encrypted_db_dir
        cache = DBCache(all_keys, db_dir)
        cache.get("contact/contact.db")
        cache.cleanup()
        assert os.path.exists(DBCache.MTIME_FILE)
