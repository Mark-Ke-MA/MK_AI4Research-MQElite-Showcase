"""Typed MQElite failures with content-free messages."""


class MQEliteError(Exception):
    """Base class for expected MQElite failures."""


class ValidationError(MQEliteError):
    """Input failed a schema or domain invariant."""


class ConflictError(MQEliteError):
    """Two immutable or canonical sources disagree."""


class StorageError(MQEliteError):
    """Protected storage could not be read or committed safely."""


class MainHistoryError(MQEliteError):
    """A content-safe failure returned by the canonical main-history adapter."""

    def __init__(self, code: str, *, retryable: bool) -> None:
        super().__init__(f"canonical main history failed: {code}")
        self.code = code
        self.retryable = retryable
