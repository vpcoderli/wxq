"""Tests for the exception hierarchy."""

from wxq.exceptions import (
    WechatQueryError,
    ConfigError,
    DecryptError,
    KeyScanError,
    QueryError,
    KeysNotFoundError,
    DataDirNotFoundError,
    KeyVerificationError,
    CorruptDatabaseError,
    ProcessNotFoundError,
    PermissionError_,
    NoKeysExtractedError,
    ChatNotFoundError,
    NoMessagesError,
    InvalidTimeRangeError,
    PaginationError,
)


class TestExceptionHierarchy:
    def test_base_is_exception(self):
        assert issubclass(WechatQueryError, Exception)

    def test_config_errors(self):
        assert issubclass(ConfigError, WechatQueryError)
        assert issubclass(KeysNotFoundError, ConfigError)
        assert issubclass(DataDirNotFoundError, ConfigError)

    def test_decrypt_errors(self):
        assert issubclass(DecryptError, WechatQueryError)
        assert issubclass(KeyVerificationError, DecryptError)
        assert issubclass(CorruptDatabaseError, DecryptError)

    def test_keyscan_errors(self):
        assert issubclass(KeyScanError, WechatQueryError)
        assert issubclass(ProcessNotFoundError, KeyScanError)
        assert issubclass(PermissionError_, KeyScanError)
        assert issubclass(NoKeysExtractedError, KeyScanError)

    def test_query_errors(self):
        assert issubclass(QueryError, WechatQueryError)
        assert issubclass(ChatNotFoundError, QueryError)
        assert issubclass(NoMessagesError, QueryError)
        assert issubclass(InvalidTimeRangeError, QueryError)
        assert issubclass(PaginationError, QueryError)

    def test_exception_message(self):
        e = KeysNotFoundError("missing keys.json")
        assert "missing keys.json" in str(e)
        assert isinstance(e, WechatQueryError)

    def test_catch_broad(self):
        """Catching WechatQueryError should catch all subtypes."""
        exceptions = [
            ConfigError("cfg"), DecryptError("dec"),
            KeyScanError("ks"), QueryError("qe"),
        ]
        for exc in exceptions:
            try:
                raise exc
            except WechatQueryError:
                pass  # should catch
