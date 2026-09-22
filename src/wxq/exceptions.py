"""Exception hierarchy for wxq.

All application-level exceptions derive from WechatQueryError,
making it easy to catch everything or handle specific categories.
"""


class WechatQueryError(Exception):
    """Base exception for all wxq errors."""


# ---- Configuration / Init ----

class ConfigError(WechatQueryError):
    """Configuration file missing or malformed."""


class KeysNotFoundError(ConfigError):
    """Encryption keys file does not exist (run init first)."""


class DataDirNotFoundError(ConfigError):
    """WeChat data directory not found."""


# ---- Decryption ----

class DecryptError(WechatQueryError):
    """Failure during database decryption."""


class KeyVerificationError(DecryptError):
    """HMAC verification of an encryption key failed."""


class CorruptDatabaseError(DecryptError):
    """Database file is too small or structurally invalid."""


# ---- Key Scanning ----

class KeyScanError(WechatQueryError):
    """Failure during memory-based key extraction."""


class ProcessNotFoundError(KeyScanError):
    """Target process (WeChat) not found."""


class PermissionError_(KeyScanError):
    """Insufficient permissions for memory scanning."""


class NoKeysExtractedError(KeyScanError):
    """Memory scan completed but no valid keys were found."""


# ---- Query ----

class QueryError(WechatQueryError):
    """Error during data query."""


class ChatNotFoundError(QueryError):
    """Chat target could not be resolved."""


class NoMessagesError(QueryError):
    """No messages found for the given query parameters."""


class InvalidTimeRangeError(QueryError):
    """Time range specification is invalid."""


class PaginationError(QueryError):
    """Invalid limit or offset values."""
