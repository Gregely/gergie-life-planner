"""Domain errors raised by data access and logic code.

They are deliberately HTTP-agnostic so the same functions can be called from
REST routers, scripts or (later) AI tools. ``mealplanner.main`` maps them to
HTTP status codes.
"""


class DomainError(Exception):
    """Base class for expected, user-facing errors."""


class NotFound(DomainError):
    """The referenced record does not exist (HTTP 404)."""


class Conflict(DomainError):
    """The request clashes with existing data (HTTP 409)."""


class Invalid(DomainError):
    """The request is well-formed but semantically invalid (HTTP 422)."""
