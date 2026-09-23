"""Optional OIDC/membership adapter for hosts that explicitly require it.

Install with ``pip install contextrail[oidc]``. This is not ContextRail core:
the preferred integration is a host-injected trusted principal and authorization
decision. The adapter exists only for deployments that ask ContextRail itself to
act as an OIDC Resource Server.
"""

from .auth import AuthorizationService, Principal
from .errors import InvalidRequest
from .host import ContextTools
from .models import Scope, integer
from .store import Store


class AuthorizedContextTools(ContextTools):
    """Read-only tool mount guarded by the optional OIDC authorization adapter."""

    def __init__(self, store: Store, scope: Scope, session: str, *, principal: Principal,
                 authorization: AuthorizationService, max_read_bytes: int = 65536):
        if not isinstance(principal, Principal) or not isinstance(authorization, AuthorizationService):
            raise InvalidRequest("Authorized tools require a verified principal and authorization service.")
        self.principal, self.authorization = principal, authorization
        super().__init__(store, scope, session, max_read_bytes=max_read_bytes)

    def call(self, name: str, arguments: dict) -> dict:
        action = {"context.get": "context.read", "context.list": "context.read",
                  "context.summary_get": "context.read", "context.search": "context.search",
                  "context.summary_search": "context.search"}.get(name)
        if action is None:
            raise InvalidRequest("Unknown context tool.")
        self.authorization.require(self.principal, self.scope, action)
        return super().call(name, arguments)
