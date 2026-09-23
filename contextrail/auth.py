"""OIDC authentication and local relationship-based authorization.

This module deliberately separates an untrusted requested Scope from a verified
Principal.  It validates access tokens before constructing a Principal, then
uses tenant/project memberships stored by ``Store`` to authorize that principal.
It does not implement login, password storage, MFA, or token issuance; Keycloak
or another standards-compliant OIDC provider owns those security boundaries.
"""

from dataclasses import dataclass
import time
from typing import Callable, Iterable
import uuid

import jwt

from .errors import AccessDenied, InvalidRequest
from .models import Scope, canonical, digest, identifier
from .store import CORE_SCHEMA_VERSION, SCHEMA_V2, Store


READ_ACTIONS = frozenset({"context.read", "context.search"})
WRITE_ACTIONS = frozenset({"artifact.write", "task.update", "action.begin", "action.finish"})
HANDOFF_ACTIONS = frozenset({"handoff.prepare", "handoff.acknowledge", "handoff.validate", "handoff.activate", "handoff.abort"})
KNOWN_ACTIONS = READ_ACTIONS | WRITE_ACTIONS | HANDOFF_ACTIONS
PROJECT_ROLES = frozenset({"admin", "editor", "viewer"})
TENANT_ROLES = frozenset({"owner", "admin", "member"})


@dataclass(frozen=True)
class Principal:
    """A stable identity key, never a caller-supplied display field."""

    issuer: str
    subject: str
    email: str | None = None
    display_name: str | None = None
    scopes: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        identifier(self.issuer)
        identifier(self.subject)
        if self.email is not None:
            identifier(self.email)
        if self.display_name is not None:
            identifier(self.display_name)
        if not isinstance(self.scopes, frozenset) or any(not isinstance(value, str) or not value for value in self.scopes):
            raise InvalidRequest("Principal scopes must be a set of nonempty strings.")

    @property
    def digest(self) -> str:
        """Stable audit correlation without writing issuer/subject into every event."""
        return digest(canonical({"issuer": self.issuer, "subject": self.subject}).encode("utf-8"))


def bearer_token(authorization: str) -> str:
    """Parse exactly one RFC 6750-style Bearer credential without logging it."""
    if not isinstance(authorization, str):
        raise AccessDenied("A bearer access token is required.")
    scheme, separator, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not separator or not token or any(char.isspace() for char in token):
        raise AccessDenied("A bearer access token is required.")
    return token


class OidcAccessTokenVerifier:
    """Validate a Keycloak-compatible JWT access token using a trusted JWKS URL.

    The issuer, audience, accepted algorithms, and required scopes are deployment
    configuration.  None are derived from token headers or request parameters.
    """

    def __init__(self, *, issuer: str, audience: str, jwks_url: str | None = None,
                 required_scopes: Iterable[str] = (), expected_token_type: str | None = "Bearer",
                 allowed_algorithms: Iterable[str] = ("RS256",), leeway: int = 30,
                 key_resolver: Callable[[str], object] | None = None):
        identifier(issuer)
        identifier(audience)
        if jwks_url is None and key_resolver is None:
            raise InvalidRequest("Configure a trusted JWKS URL or explicit key resolver.")
        if jwks_url is not None:
            identifier(jwks_url)
        algorithms = frozenset(allowed_algorithms)
        if not algorithms or any(value not in {"RS256", "RS384", "RS512", "ES256", "ES384", "ES512"} for value in algorithms):
            raise InvalidRequest("Allowed JWT algorithms must be an explicit asymmetric allowlist.")
        if type(leeway) is not int or leeway < 0 or leeway > 300:
            raise InvalidRequest("JWT leeway must be an integer between zero and 300 seconds.")
        required = frozenset(required_scopes)
        if any(not isinstance(value, str) or not value for value in required):
            raise InvalidRequest("Required scopes must be nonempty strings.")
        if expected_token_type is not None:
            identifier(expected_token_type)
        self.issuer = issuer
        self.audience = audience
        self.required_scopes = required
        self.expected_token_type = expected_token_type
        self.allowed_algorithms = algorithms
        self.leeway = leeway
        self._resolver = key_resolver
        self._jwks = jwt.PyJWKClient(jwks_url, cache_keys=True, lifespan=300) if jwks_url else None

    def verify_bearer(self, authorization: str) -> Principal:
        return self.verify(bearer_token(authorization))

    def verify(self, token: str) -> Principal:
        if not isinstance(token, str) or not token or len(token) > 16_384:
            raise AccessDenied("Access token is invalid.")
        try:
            header = jwt.get_unverified_header(token)
            algorithm = header.get("alg")
            if algorithm not in self.allowed_algorithms:
                raise AccessDenied("Access token is invalid.")
            key = self._resolver(token) if self._resolver else self._jwks.get_signing_key_from_jwt(token).key
            claims = jwt.decode(
                token, key=key, algorithms=sorted(self.allowed_algorithms), audience=self.audience,
                issuer=self.issuer, leeway=self.leeway,
                options={"require": ["exp", "iat", "iss", "sub", "aud"]},
            )
        except AccessDenied:
            raise
        except jwt.PyJWTError:
            raise AccessDenied("Access token is invalid.") from None
        except Exception:
            # Network/JWKS and key parsing details must not cross the API boundary.
            raise AccessDenied("Access token is invalid.") from None
        if self.expected_token_type is not None and claims.get("typ") != self.expected_token_type:
            raise AccessDenied("Access token is invalid.")
        scopes = self._scopes(claims)
        if not self.required_scopes <= scopes:
            raise AccessDenied("Access token lacks a required scope.")
        email = claims.get("email") if isinstance(claims.get("email"), str) else None
        display_name = claims.get("name") if isinstance(claims.get("name"), str) else None
        try:
            return Principal(claims["iss"], claims["sub"], email, display_name, scopes)
        except InvalidRequest:
            raise AccessDenied("Access token is invalid.") from None

    @staticmethod
    def _scopes(claims: dict) -> frozenset[str]:
        scope = claims.get("scope", "")
        if isinstance(scope, str):
            values = scope.split()
        elif isinstance(scope, list) and all(isinstance(value, str) for value in scope):
            values = scope
        else:
            raise AccessDenied("Access token is invalid.")
        return frozenset(values)


class AuthorizationService:
    """Tenant/project authorization backed by ContextRail's transactional store.

    Administration methods are intended for a trusted control plane after it has
    authorized its own administrator. Application request paths should call
    ``require`` for every operation using a verified Principal.
    """

    def __init__(self, store: Store):
        self.store = store
        # The OIDC/membership adapter owns its tables. Core migrations may move
        # the shared database forward without making identity administration a
        # core requirement, so table presence (not only user_version) is checked.
        with self.store.transaction():
            version = self.store.db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (2, CORE_SCHEMA_VERSION):
                raise InvalidRequest("OIDC adapter requires a supported ContextRail database schema.")
            has_identities = self.store.db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='identities'").fetchone() is not None
            if not has_identities:
                for statement in SCHEMA_V2.split(";"):
                    if statement.strip():
                        self.store.db.execute(statement)

    def register_identity(self, principal: Principal) -> str:
        with self.store.transaction():
            row = self.store.db.execute("SELECT id FROM identities WHERE issuer=? AND subject=?",
                                        (principal.issuer, principal.subject)).fetchone()
            if row is not None:
                self.store.db.execute("UPDATE identities SET email=?,display_name=? WHERE id=?",
                                      (principal.email, principal.display_name, row["id"]))
                return row["id"]
            identity_id = str(uuid.uuid4())
            self.store.db.execute("INSERT INTO identities VALUES(?,?,?,?,?,?)",
                                  (identity_id, principal.issuer, principal.subject, principal.email,
                                   principal.display_name, self.store.clock()))
            return identity_id

    def create_tenant(self, tenant_id: str) -> None:
        identifier(tenant_id)
        with self.store.transaction():
            self.store.db.execute("INSERT INTO tenants VALUES(?,?)", (tenant_id, self.store.clock()))

    def create_project(self, tenant_id: str, project_id: str) -> None:
        identifier(tenant_id)
        identifier(project_id)
        with self.store.transaction():
            self.store.db.execute("INSERT INTO projects VALUES(?,?,?)", (tenant_id, project_id, self.store.clock()))

    def grant_tenant_role(self, principal: Principal, tenant_id: str, role: str) -> None:
        identifier(tenant_id)
        if role not in TENANT_ROLES:
            raise InvalidRequest("Unknown tenant role.")
        identity_id = self.register_identity(principal)
        with self.store.transaction():
            self.store.db.execute("INSERT INTO tenant_memberships VALUES(?,?,?,?) "
                                  "ON CONFLICT(tenant_id,identity_id) DO UPDATE SET role=excluded.role",
                                  (tenant_id, identity_id, role, self.store.clock()))

    def grant_project_role(self, principal: Principal, tenant_id: str, project_id: str, role: str) -> None:
        identifier(tenant_id)
        identifier(project_id)
        if role not in PROJECT_ROLES:
            raise InvalidRequest("Unknown project role.")
        identity_id = self.register_identity(principal)
        with self.store.transaction():
            self.store.db.execute("INSERT INTO project_memberships VALUES(?,?,?,?,?) "
                                  "ON CONFLICT(tenant_id,project_id,identity_id) DO UPDATE SET role=excluded.role",
                                  (tenant_id, project_id, identity_id, role, self.store.clock()))

    def revoke_project_role(self, principal: Principal, tenant_id: str, project_id: str) -> None:
        identity_id = self._identity_id(principal)
        with self.store.transaction():
            self.store.db.execute("DELETE FROM project_memberships WHERE tenant_id=? AND project_id=? AND identity_id=?",
                                  (tenant_id, project_id, identity_id))

    def require(self, principal: Principal, scope: Scope, action: str) -> None:
        if action not in KNOWN_ACTIONS:
            raise InvalidRequest("Unknown authorization action.")
        with self.store.transaction():
            identity_id = self._identity_id(principal, deny_if_missing=False)
            project = self.store.db.execute("SELECT 1 FROM projects WHERE tenant_id=? AND id=?",
                                            (scope.tenant, scope.project)).fetchone()
            tenant_role = None
            project_role = None
            if identity_id is not None:
                row = self.store.db.execute("SELECT role FROM tenant_memberships WHERE tenant_id=? AND identity_id=?",
                                            (scope.tenant, identity_id)).fetchone()
                tenant_role = row["role"] if row else None
                row = self.store.db.execute("SELECT role FROM project_memberships WHERE tenant_id=? AND project_id=? AND identity_id=?",
                                            (scope.tenant, scope.project, identity_id)).fetchone()
                project_role = row["role"] if row else None
            allowed = project is not None and self._allowed(tenant_role, project_role, action)
            self.store.db.execute("INSERT INTO authorization_events(principal_digest,tenant_id,project_id,action,decision,at) VALUES(?,?,?,?,?,?)",
                                  (principal.digest, scope.tenant, scope.project, action,
                                   "allow" if allowed else "deny", self.store.clock()))
        # Raise after committing the decision record: denial audit must not vanish
        # merely because the request receives an AccessDenied response.
        if not allowed:
            raise AccessDenied("The authenticated principal is not authorized for this resource.")

    def audit_events(self) -> list[dict]:
        return [dict(row) for row in self.store.db.execute(
            "SELECT sequence,principal_digest,tenant_id,project_id,action,decision,at FROM authorization_events ORDER BY sequence")]

    def _identity_id(self, principal: Principal, *, deny_if_missing: bool = True) -> str | None:
        row = self.store.db.execute("SELECT id FROM identities WHERE issuer=? AND subject=?",
                                    (principal.issuer, principal.subject)).fetchone()
        if row is None and deny_if_missing:
            raise AccessDenied("The authenticated principal is not provisioned.")
        return row["id"] if row else None

    @staticmethod
    def _allowed(tenant_role: str | None, project_role: str | None, action: str) -> bool:
        if tenant_role in {"owner", "admin"}:
            return True
        if project_role == "admin":
            return True
        if project_role == "editor":
            return action in READ_ACTIONS | WRITE_ACTIONS | HANDOFF_ACTIONS
        if project_role == "viewer":
            return action in READ_ACTIONS
        return False
