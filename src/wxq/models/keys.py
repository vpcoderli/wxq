"""Key and DB file models for encryption/decryption."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class KeyInfo:
    """Encryption key information for a database file."""

    enc_key: str  # hex-encoded AES-256 key
    salt: str     # hex-encoded 16-byte salt
    size_mb: float = 0.0


@dataclass(frozen=True, slots=True)
class DBFileInfo:
    """A database file discovered during key scanning."""

    rel_path: str
    abs_path: str
    size: int
    salt_hex: str
    page1: bytes
