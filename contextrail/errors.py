"""Stable, content-free errors safe to return across the host boundary."""


class RailError(Exception):
    code = "rail_error"


class InvalidRequest(RailError):
    code = "invalid_request"


class NotFound(RailError):
    code = "not_found"


class Conflict(RailError):
    code = "conflict"


class AccessDenied(RailError):
    code = "access_denied"


class Unavailable(RailError):
    code = "unavailable"


class StaleSnapshot(Conflict):
    code = "stale_snapshot"


class BudgetExceeded(RailError):
    code = "budget_exceeded"


class IntegrityError(RailError):
    code = "integrity_error"
