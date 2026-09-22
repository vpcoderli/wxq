"""Adversarial and security-focused tests.

Covers hostile or malformed input across the trust boundaries:
- crypto with wrong keys / truncated / corrupt data
- SQL-injection safety of message-table name handling
- path-traversal rejection in key lookup
- unicode and binary content edge cases
"""

import os
import struct

import pytest

from wxq.core.crypto import (
    PAGE_SZ,
    RESERVE_SZ,
    decrypt_page,
    decrypt_wal,
    full_decrypt,
)
from wxq.core.key_utils import get_key_info
from wxq.exceptions import CorruptDatabaseError, DecryptError
from wxq.services.message_service import _is_safe_msg_table_name
from wxq.services.message_parser import decompress_content, parse_message_content


class TestCryptoRobustness:
    def test_short_page_raises_corrupt(self):
        with pytest.raises(CorruptDatabaseError):
            decrypt_page(os.urandom(32), b"\x00" * 100, 1)

    def test_decrypt_page_bad_key_length(self):
        # 20 bytes is not a valid AES key length; must fail explicitly
        with pytest.raises(DecryptError):
            decrypt_page(os.urandom(20), b"\x00" * PAGE_SZ, 1)

    def test_decrypt_page_aes128_key_rejected(self):
        # 16 bytes is valid AES-128 but wrong for SQLCipher 4 (AES-256)
        with pytest.raises(DecryptError):
            decrypt_page(os.urandom(16), b"\x00" * PAGE_SZ, 1)

    def test_wrong_key_length_in_full_decrypt(self, tmp_path):
        # A valid-sized file but a key of the wrong length -> AES rejects -> DecryptError
        db = tmp_path / "x.db"
        db.write_bytes(os.urandom(PAGE_SZ))
        out = tmp_path / "out" / "x.db"
        with pytest.raises(DecryptError):
            full_decrypt(str(db), str(out), os.urandom(16))  # 16 != 32

    def test_file_too_small_raises(self, tmp_path):
        db = tmp_path / "tiny.db"
        db.write_bytes(b"\x00" * 100)
        out = tmp_path / "out" / "tiny.db"
        with pytest.raises(CorruptDatabaseError):
            full_decrypt(str(db), str(out), os.urandom(32))

    def test_garbage_decrypts_without_crash(self, tmp_path):
        """A well-sized random file decrypts to garbage but must not crash.

        (Corruption is detected later at SQLite-open time, not here.)
        """
        db = tmp_path / "g.db"
        db.write_bytes(os.urandom(PAGE_SZ * 2))
        out = tmp_path / "out" / "g.db"
        pages = full_decrypt(str(db), str(out), os.urandom(32))
        assert pages == 2
        assert os.path.getsize(str(out)) == PAGE_SZ * 2

    def test_decrypt_wal_missing_file(self, tmp_path):
        assert decrypt_wal(str(tmp_path / "none.db-wal"), str(tmp_path / "x"), os.urandom(32)) == 0

    def test_decrypt_wal_header_only(self, tmp_path):
        wal = tmp_path / "x.db-wal"
        wal.write_bytes(b"\x00" * 20)  # < WAL_HEADER_SZ (32)
        main = tmp_path / "main.db"
        main.write_bytes(b"\x00" * PAGE_SZ)
        assert decrypt_wal(str(wal), str(main), os.urandom(32)) == 0

    def test_decrypt_wal_corrupt_frames_no_crash(self, tmp_path):
        # Full 32-byte header + one junk frame; salts won't match -> skipped, no crash
        wal = tmp_path / "x.db-wal"
        header = b"\x37\x7f\x06\x82" + os.urandom(28)
        junk_frame = os.urandom(24 + PAGE_SZ)
        wal.write_bytes(header + junk_frame)
        main = tmp_path / "main.db"
        main.write_bytes(b"\x00" * PAGE_SZ)
        # Frame salt won't match header salt -> 0 patched, but must not raise
        patched = decrypt_wal(str(wal), str(main), os.urandom(32))
        assert patched == 0


class TestSqlInjectionSafety:
    def test_valid_table_name_accepted(self):
        assert _is_safe_msg_table_name("Msg_" + "a" * 32) is True
        assert _is_safe_msg_table_name("Msg_0123456789abcdef0123456789abcdef") is True

    def test_injection_attempts_rejected(self):
        hostile = [
            "Msg_abc; DROP TABLE contact;--",
            "Msg_' OR '1'='1",
            "Msg_" + "a" * 32 + "; DELETE FROM x",
            "Msg_",                      # too short
            "Msg_XYZ",                   # non-hex
            "Msg_" + "A" * 32,           # uppercase hex not allowed by pattern
            "sqlite_master",
            "Msg_" + "a" * 31,           # 31 chars
            "Msg_" + "a" * 33,           # 33 chars
            "[Msg_" + "a" * 32 + "]",    # bracket escape attempt
        ]
        for name in hostile:
            assert _is_safe_msg_table_name(name) is False, name


class TestPathTraversalSafety:
    def test_traversal_key_rejected(self):
        keys = {"contact/contact.db": {"enc_key": "aa"}}
        # Attempting to escape the key namespace must not match
        assert get_key_info(keys, "../../../etc/passwd") is None

    def test_absolute_path_rejected(self):
        keys = {"contact/contact.db": {"enc_key": "aa"}}
        assert get_key_info(keys, "/etc/passwd") is None


class TestContentEdgeCases:
    def test_emoji_content(self):
        sender, text = parse_message_content("Hello 👋🎉 世界", 1, False)
        assert "👋" in text
        assert "世界" in text

    def test_null_bytes_in_bytes_content(self):
        result = decompress_content(b"a\x00b\x00c", None)
        assert result is not None  # decoded with errors=replace, no crash

    def test_invalid_utf8_bytes(self):
        # Lone continuation byte — must decode with replacement, not raise
        result = decompress_content(b"\xff\xfe\xfd", None)
        assert result is not None

    def test_very_long_content(self):
        long = "x" * 100000
        sender, text = parse_message_content(long, 1, False)
        assert len(text) == 100000

    def test_group_prefix_with_colon_in_body(self):
        # Only the FIRST ":\n" splits sender from body
        sender, text = parse_message_content("wxid_a:\ntime is 3:30\nok", 1, True)
        assert sender == "wxid_a"
        assert "3:30" in text
