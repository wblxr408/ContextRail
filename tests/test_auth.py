import time
import sqlite3
import unittest
from contextlib import closing
from pathlib import Path

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from contextrail import Selection, Store
from contextrail.auth import AuthorizationService, OidcAccessTokenVerifier, Principal
from contextrail.oidc_adapter import AuthorizedContextTools
from contextrail.errors import AccessDenied
from contextrail.store import APPLICATION_ID, CORE_SCHEMA_VERSION, SCHEMA_V1
from .common import RailTest


class OidcTests(unittest.TestCase):
    def setUp(self):
        self.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.public_key = self.private_key.public_key()
        self.issuer = "https://login.example.test/realms/contextrail"
        self.verifier = OidcAccessTokenVerifier(
            issuer=self.issuer, audience="contextrail-api", required_scopes=("context.read",),
            key_resolver=lambda token: self.public_key,
        )

    def token(self, **changes):
        now = int(time.time())
        claims = {"iss": self.issuer, "sub": "user-8f72", "aud": "contextrail-api",
                  "exp": now + 300, "iat": now, "typ": "Bearer", "scope": "context.read profile",
                  "email": "person@example.test", "name": "Test Person"}
        claims.update(changes)
        return jwt.encode(claims, self.private_key, algorithm="RS256", headers={"kid": "test-key"})

    def test_valid_token_becomes_stable_principal(self):
        principal = self.verifier.verify_bearer("Bearer " + self.token())
        self.assertEqual((principal.issuer, principal.subject), (self.issuer, "user-8f72"))
        self.assertEqual(principal.email, "person@example.test")
        self.assertIn("context.read", principal.scopes)
        self.assertEqual(principal.digest, Principal(self.issuer, "user-8f72").digest)

    def test_invalid_or_insufficient_tokens_are_rejected_without_echo(self):
        cases = [
            self.token(aud="another-api"), self.token(iss="https://attacker.example"),
            self.token(exp=int(time.time()) - 60), self.token(scope="profile"), self.token(typ="ID"),
            "not-a-jwt",
        ]
        for token in cases:
            with self.subTest(token_type=type(token).__name__), self.assertRaises(AccessDenied) as caught:
                self.verifier.verify(token)
            self.assertNotIn(str(token), str(caught.exception))
        for header in ("Basic abc", "Bearer", "Bearer token extra", ""):
            with self.subTest(header=header), self.assertRaises(AccessDenied):
                self.verifier.verify_bearer(header)


class AuthorizationTests(RailTest):
    def setUp(self):
        super().setUp()
        self.authorization = AuthorizationService(self.store)
        self.principal = Principal("https://login.example.test/realms/contextrail", "user-8f72")
        self.other = Principal("https://login.example.test/realms/contextrail", "user-other")
        self.authorization.create_tenant(self.scope.tenant)
        self.authorization.create_project(self.scope.tenant, self.scope.project)

    def test_requested_scope_is_authorized_against_trusted_principal(self):
        ref = self.put(content=b"private evidence")
        self.authorization.grant_project_role(self.principal, self.scope.tenant, self.scope.project, "viewer")
        tools = AuthorizedContextTools(self.store, self.scope, self.a.session, principal=self.principal,
                                       authorization=self.authorization)
        self.assertEqual(tools.call("context.get", {"name": ref.name, "revision": ref.revision})["content"],
                         "private evidence")
        snapshot = self.snapshot(Selection(ref))
        self.assertEqual(tools.call("context.list", {"snapshot": snapshot})["total"], 1)
        with self.assertRaises(AccessDenied):
            self.authorization.require(self.other, self.scope, "context.read")
        requested_other_project = type(self.scope)(self.scope.tenant, "other-project", self.scope.branch, self.scope.task)
        with self.assertRaises(AccessDenied):
            self.authorization.require(self.principal, requested_other_project, "context.read")
        events = self.authorization.audit_events()
        self.assertTrue(any(event["decision"] == "allow" for event in events))
        self.assertTrue(any(event["decision"] == "deny" for event in events))
        self.assertNotIn(self.principal.subject, str(events))

    def test_each_tool_call_rechecks_authorization_after_revocation(self):
        ref = self.put(content=b"protected")
        self.authorization.grant_project_role(self.principal, self.scope.tenant, self.scope.project, "viewer")
        tools = AuthorizedContextTools(self.store, self.scope, self.a.session, principal=self.principal,
                                       authorization=self.authorization)
        self.assertEqual(tools.call("context.get", {"name": ref.name, "revision": ref.revision})["content"], "protected")
        self.authorization.revoke_project_role(self.principal, self.scope.tenant, self.scope.project)
        with self.assertRaises(AccessDenied):
            tools.call("context.search", {"query": "protected"})

    def test_roles_have_explicit_capabilities(self):
        self.authorization.grant_project_role(self.principal, self.scope.tenant, self.scope.project, "editor")
        for action in ("context.read", "artifact.write", "handoff.activate"):
            self.authorization.require(self.principal, self.scope, action)
        self.authorization.grant_project_role(self.principal, self.scope.tenant, self.scope.project, "viewer")
        with self.assertRaises(AccessDenied):
            self.authorization.require(self.principal, self.scope, "artifact.write")
        self.authorization.grant_tenant_role(self.principal, self.scope.tenant, "admin")
        self.authorization.require(self.principal, self.scope, "handoff.activate")

    def test_schema_v1_migrates_membership_tables_atomically(self):
        old = Path(self.directory.name) / "v1.sqlite3"
        with closing(sqlite3.connect(old)) as database:
            for statement in SCHEMA_V1.split(";"):
                if statement.strip():
                    database.execute(statement)
            database.execute("PRAGMA user_version=1")
            database.execute(f"PRAGMA application_id={APPLICATION_ID}")
            database.commit()
        with Store(old) as migrated:
            self.assertEqual(migrated.db.execute("PRAGMA user_version").fetchone()[0], CORE_SCHEMA_VERSION)
            AuthorizationService(migrated)
            self.assertEqual(migrated.db.execute("PRAGMA user_version").fetchone()[0], CORE_SCHEMA_VERSION)
            tables = {row[0] for row in migrated.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertTrue({"identities", "tenant_memberships", "authorization_events"} <= tables)
