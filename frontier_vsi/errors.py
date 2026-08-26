from __future__ import annotations


class FrontierVSIError(Exception):
    """Base class for stable FrontierVSI domain errors."""


class RevisionConflictError(FrontierVSIError):
    """Raised when a mutation is based on a stale project revision."""


class InvalidArtifactPathError(FrontierVSIError):
    """Raised when a canonical artifact path escapes the project namespace."""


class ProjectLockedError(FrontierVSIError):
    """Raised when another process owns the project mutation lock."""


class IdempotencyConflictError(FrontierVSIError):
    """Raised when one request id is reused for a different command."""


class RequestNotFoundError(FrontierVSIError):
    """Raised when an idempotency request record does not exist."""
