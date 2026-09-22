"""Tests for the SQLCipher decryption engine."""

import os
import struct

import pytest

from wxq.core.crypto import (
    PAGE_SZ,
    KEY_SZ,
    SALT_SZ,
    RESERVE_SZ,
    SQLITE_HDR,
    WAL_HEADER_SZ,
    WAL_FRAME_HEADER_SZ,
    decrypt_page,
    full_decrypt,
    decrypt_wal,
)
from wxq.exceptions import CorruptDatabaseError, DecryptError


class TestDecryptPage:
    def test_page1_returns_sqlite_header(self):
        """Page 1 decryption should produce SQLite header."""
        key = os.urandom(32)
        # Build a valid page 1: salt(16) + encrypted_data + iv(16) + hmac(64)
        salt = os.urandom(SALT_SZ)
        iv = os.urandom(16)
        # Encrypt some data
        from Crypto.Cipher import AES
        data_len = PAGE_SZ - SALT_SZ - RESERVE_SZ
        plaintext = b"\x00" * data_len
        cipher = AES.new(key, AES.MODE_CBC, iv)
        encrypted = cipher.encrypt(plaintext)
        hmac_placeholder = b"\x00" * 64
        page = salt + encrypted + iv + hmac_placeholder
        assert len(page) == PAGE_SZ

        result = decrypt_page(key, page, 1)
        assert len(result) == PAGE_SZ
        assert result[:len(SQLITE_HDR)] == SQLITE_HDR

    def test_page2_returns_correct_length(self):
        """Non-page-1 decryption should return PAGE_SZ bytes."""
        key = os.urandom(32)
        iv = os.urandom(16)
        from Crypto.Cipher import AES
        data_len = PAGE_SZ - RESERVE_SZ
        plaintext = b"\xAB" * data_len
        cipher = AES.new(key, AES.MODE_CBC, iv)
        encrypted = cipher.encrypt(plaintext)
        hmac_placeholder = b"\x00" * 64
        page = encrypted + iv + hmac_placeholder
        assert len(page) == PAGE_SZ

        result = decrypt_page(key, page, 2)
        assert len(result) == PAGE_SZ

    def test_short_page_raises(self):
        key = os.urandom(32)
        with pytest.raises(CorruptDatabaseError, match="expected 4096"):
            decrypt_page(key, b"\x00" * 100, 1)


class TestFullDecrypt:
    def test_file_too_small(self, tmp_path):
        db_path = str(tmp_path / "tiny.db")
        with open(db_path, "wb") as f:
            f.write(b"\x00" * 100)
        with pytest.raises(CorruptDatabaseError, match="too small"):
            full_decrypt(db_path, str(tmp_path / "out.db"), os.urandom(32))

    def test_single_page_roundtrip(self, tmp_path, fake_encrypted_db):
        db_path, enc_key_hex, enc_key = fake_encrypted_db
        out_path = str(tmp_path / "decrypted.db")
        pages = full_decrypt(db_path, out_path, enc_key)
        assert pages == 1
        assert os.path.exists(out_path)
        with open(out_path, "rb") as f:
            content = f.read()
        assert len(content) == PAGE_SZ
        assert content[:len(SQLITE_HDR)] == SQLITE_HDR

    def test_creates_output_dir(self, tmp_path, fake_encrypted_db):
        db_path, _, enc_key = fake_encrypted_db
        out_dir = tmp_path / "nested" / "dir"
        out_path = str(out_dir / "out.db")
        full_decrypt(db_path, out_path, enc_key)
        assert os.path.exists(out_path)


class TestDecryptWal:
    def test_no_wal_returns_zero(self, tmp_path):
        wal_path = str(tmp_path / "nonexistent.db-wal")
        out_path = str(tmp_path / "out.db")
        assert decrypt_wal(wal_path, out_path, os.urandom(32)) == 0

    def test_empty_wal_returns_zero(self, tmp_path):
        wal_path = str(tmp_path / "empty.db-wal")
        with open(wal_path, "wb") as f:
            f.write(b"\x00" * 10)  # smaller than WAL header
        out_path = str(tmp_path / "out.db")
        with open(out_path, "wb") as f:
            f.write(b"\x00" * PAGE_SZ)  # need a valid file to open r+b
        assert decrypt_wal(wal_path, out_path, os.urandom(32)) == 0


class TestConstants:
    def test_page_size(self):
        assert PAGE_SZ == 4096

    def test_key_size(self):
        assert KEY_SZ == 32

    def test_salt_size(self):
        assert SALT_SZ == 16

    def test_reserve_size(self):
        assert RESERVE_SZ == 80  # 16 IV + 64 HMAC

    def test_sqlite_header(self):
        assert SQLITE_HDR == b"SQLite format 3\x00"
