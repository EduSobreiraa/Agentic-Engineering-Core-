"""Public ACP errors."""


class ACPError(Exception):
    """Base error for rejected ACP operations."""


class AuthorizationError(ACPError):
    """The handle, sender, recipient, or operation is unauthorized."""


class ConflictError(ACPError):
    """An identifier already names different content."""


class StateError(ACPError):
    """The delivery cannot make the requested transition."""
