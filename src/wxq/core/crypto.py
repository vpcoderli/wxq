"""SQLCipher 4 decryption engine — AES-256-CBC with HMAC-SHA512 verification.

Constants
---------
- PAGE_SZ = 4096 — SQLCipher page size
- KEY_SZ  = 32   — AES-256 key length
- SALT_SZ = 16   — Salt prefix on page 1
- RESERVE_SZ = 80 — IV (16 bytes) + HMAC-SHA512 (64 bytes) per page
"""

from __future__ import annotations

import os
import struct
from typing import BinaryIO

from Crypto.Cipher import AES

from ..exceptions import CorruptDatabaseError, DecryptError

PAGE_SZ = 4096
KEY_SZ = 32
SALT_SZ = 16
RESERVE_SZ = 80  # IV(16) + HMAC-SHA512(64)
SQLITE_HDR = b"SQLite format 3\x00"
WAL_HEADER_SZ = 32
WAL_FRAME_HEADER_SZ = 24


def _validate_key(enc_key: bytes) -> None:
    """Ensure the key is exactly the AES-256 length SQLCipher 4 requires.

    Without this, a 16- or 24-byte key would be silently accepted as
    AES-128/192 and produce garbage instead of a clear failure.
    """
    if len(enc_key) != KEY_SZ:
        raise DecryptError(
            f"Invalid key length: {len(enc_key)} bytes (expected {KEY_SZ})"
        )


def decrypt_page(enc_key: bytes, page_data: bytes, pgno: int) -> bytes:
    """Decrypt a single SQLCipher page.

    Args:
        enc_key: 32-byte AES key.
        page_data: Raw encrypted page (PAGE_SZ bytes).
        pgno: 1-based page number.

    Returns:
        Decrypted page data (PAGE_SZ bytes).
    """
    _validate_key(enc_key)
    if len(page_data) < PAGE_SZ:
        raise CorruptDatabaseError(
            f"Page {pgno} is {len(page_data)} bytes, expected {PAGE_SZ}"
        )

    iv = page_data[PAGE_SZ - RESERVE_SZ : PAGE_SZ - RESERVE_SZ + 16]

    if pgno == 1:
        encrypted = page_data[SALT_SZ : PAGE_SZ - RESERVE_SZ]
        cipher = AES.new(enc_key, AES.MODE_CBC, iv)
        decrypted = cipher.decrypt(encrypted)
        return bytes(bytearray(SQLITE_HDR + decrypted + b"\x00" * RESERVE_SZ))
    else:
        encrypted = page_data[: PAGE_SZ - RESERVE_SZ]
        cipher = AES.new(enc_key, AES.MODE_CBC, iv)
        decrypted = cipher.decrypt(encrypted)
        return decrypted + b"\x00" * RESERVE_SZ


def full_decrypt(db_path: str, out_path: str, enc_key: bytes) -> int:
    """Decrypt an entire SQLCipher database.

    Args:
        db_path: Path to encrypted .db file.
        out_path: Path for decrypted output.
        enc_key: 32-byte AES key.

    Returns:
        Number of pages decrypted.

    Raises:
        CorruptDatabaseError: If file is too small or pages are incomplete.
        DecryptError: If decryption fails.
    """
    _validate_key(enc_key)
    file_size = os.path.getsize(db_path)
    if file_size < PAGE_SZ:
        raise CorruptDatabaseError(
            f"Database too small: {file_size} bytes (minimum {PAGE_SZ})"
        )

    total_pages = file_size // PAGE_SZ
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    try:
        with open(db_path, "rb") as fin, open(out_path, "wb") as fout:
            for pgno in range(1, total_pages + 1):
                page = fin.read(PAGE_SZ)
                if len(page) < PAGE_SZ:
                    if len(page) > 0:
                        page = page + b"\x00" * (PAGE_SZ - len(page))
                    else:
                        break
                fout.write(decrypt_page(enc_key, page, pgno))
    except CorruptDatabaseError:
        raise
    except Exception as e:
        raise DecryptError(f"Decryption failed for {db_path}: {e}") from e

    return total_pages


def decrypt_wal(wal_path: str, out_path: str, enc_key: bytes) -> int:
    """Decrypt WAL journal and patch frames into the main decrypted DB.

    Args:
        wal_path: Path to .db-wal file.
        out_path: Path to the already-decrypted main .db file (modified in place).
        enc_key: 32-byte AES key.

    Returns:
        Number of WAL frames patched.
    """
    if not os.path.exists(wal_path):
        return 0
    _validate_key(enc_key)
    wal_size = os.path.getsize(wal_path)
    if wal_size <= WAL_HEADER_SZ:
        return 0

    patched = 0
    try:
        with open(wal_path, "rb") as wf, open(out_path, "r+b") as df:
            wal_hdr = wf.read(WAL_HEADER_SZ)
            if len(wal_hdr) < WAL_HEADER_SZ:
                return 0

            wal_salt1 = struct.unpack(">I", wal_hdr[16:20])[0]
            wal_salt2 = struct.unpack(">I", wal_hdr[20:24])[0]
            frame_size = WAL_FRAME_HEADER_SZ + PAGE_SZ

            while wf.tell() + frame_size <= wal_size:
                fh = wf.read(WAL_FRAME_HEADER_SZ)
                if len(fh) < WAL_FRAME_HEADER_SZ:
                    break

                pgno = struct.unpack(">I", fh[0:4])[0]
                frame_salt1 = struct.unpack(">I", fh[8:12])[0]
                frame_salt2 = struct.unpack(">I", fh[12:16])[0]
                ep = wf.read(PAGE_SZ)
                if len(ep) < PAGE_SZ:
                    break

                # Validity checks
                if pgno == 0 or pgno > 1_000_000:
                    continue
                if frame_salt1 != wal_salt1 or frame_salt2 != wal_salt2:
                    continue

                dec = decrypt_page(enc_key, ep, pgno)
                df.seek((pgno - 1) * PAGE_SZ)
                df.write(dec)
                patched += 1
    except Exception as e:
        raise DecryptError(f"WAL decryption failed for {wal_path}: {e}") from e

    return patched
