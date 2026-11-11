"""Explicit terminal aliases for exact task snapshots in the local store."""

from dataclasses import asdict, replace
import json
import re

from .errors import BudgetExceeded, Conflict, InvalidRequest, NotFound, StaleSnapshot, Unavailable
from .models import Scope, canonical, identifier, integer, unicode_text
from .navigation import TaskNavigation


_SCHEMA = """CREATE TABLE IF NOT EXISTS task_bookmarks(
    namespace TEXT NOT NULL, name TEXT NOT NULL, scope TEXT NOT NULL REFERENCES tasks(scope),
    snapshot TEXT NOT NULL, task_version INTEGER NOT NULL, query TEXT, created REAL NOT NULL, last_opened REAL,
    PRIMARY KEY(namespace,name))"""
_VISIBLE = """SELECT b.*, t.identity, t.version AS current_version, s.version AS snapshot_version
    FROM task_bookmarks b JOIN tasks t ON t.scope=b.scope
    JOIN sessions r ON r.scope=b.scope AND r.id=?
    LEFT JOIN snapshots s ON s.scope=b.scope AND s.id=b.snapshot
    WHERE b.namespace=? AND r.provider IN (SELECT value FROM json_each(t.providers_json))"""
_NAME = re.compile(r"\w[\w.-]{0,63}")


def _name(value):
    if not isinstance(value, str) or not _NAME.fullmatch(value):
        raise InvalidRequest("Bookmark names require 1-64 letters, numbers, underscores, dots or hyphens.")
    unicode_text(value)


def _view(row):
    state = ("unavailable" if row["snapshot_version"] is None else
             "stale" if row["task_version"] != row["current_version"] or row["snapshot_version"] != row["task_version"]
             else "store_current")
    return {"name": row["name"], "scope": json.loads(row["identity"]), "snapshot": row["snapshot"],
            "task_version": row["task_version"], "query": row["query"], "state": state,
            "created_at": row["created"], "last_opened_at": row["last_opened"]}


class Bookmarks:
    def __init__(self, store, config, session):
        self.store, self.config, self.scope, self.session = store, config, config.scope, session
        self.namespace = canonical([self.scope.tenant, self.scope.project, self.scope.branch])

    def _present(self):
        return self.store.db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='task_bookmarks'").fetchone()

    def _visible(self, name):
        row = self.store.db.execute(_VISIBLE + " AND b.name=?", (self.session, self.namespace, name)).fetchone() if self._present() else None
        if row is None:
            raise NotFound("No visible bookmark with that name in this project worktree.")
        return row

    def set(self, name, snapshot, lease, query=None):
        _name(name)
        identifier(snapshot)
        if query is not None:
            unicode_text(query)
            if not query.strip() or len(query) > 1024:
                raise InvalidRequest("Bookmark search requires 1-1024 characters.")
        with self.store.transaction():
            owner = self.store._lease(self.scope, lease)
            self.store.load_snapshot(self.scope, snapshot)
            self.store.db.execute(_SCHEMA)
            prior = self.store.db.execute("SELECT scope,snapshot,query FROM task_bookmarks WHERE namespace=? AND name=?",
                                          (self.namespace, name)).fetchone()
            if prior:
                if tuple(prior) != (self.scope.key, snapshot, query):
                    raise Conflict("Bookmark already pins another selection. Remove it explicitly before reusing the name.")
            else:
                self.store.db.execute("INSERT INTO task_bookmarks VALUES(?,?,?,?,?,?,?,NULL)",
                                      (self.namespace, name, self.scope.key, snapshot, owner["version"], query, self.store.clock()))
            return _view(self._visible(name))

    def list(self, *, prefix="", limit=20, offset=0, budget=4096):
        if prefix:
            _name(prefix)
        integer(limit, 1)
        integer(offset)
        integer(budget, 1)
        if limit > 100 or offset > 1_000_000:
            raise InvalidRequest("Bookmark page limits exceeded.")
        entries, total = [], 0
        with self.store.read_transaction():
            if self._present():
                query = _VISIBLE + " AND substr(b.name,1,length(?))=?"
                args = (self.session, self.namespace, prefix, prefix)
                total = self.store.db.execute("SELECT count(*) FROM (" + query + ")", args).fetchone()[0]
                rows = self.store.db.execute(
                    query + " ORDER BY coalesce(b.last_opened,b.created) DESC,b.name LIMIT ? OFFSET ?",
                    (*args, limit, offset))
                entries = [_view(row) for row in rows]
        result = {"schema": "contextrail.bookmarks.v1", "entries": entries, "total": total, "offset": offset}
        while True:
            result["next_offset"] = offset + len(entries) if offset + len(entries) < total else None
            if len(canonical(result).encode("utf-8")) <= budget:
                return result
            if not entries:
                raise BudgetExceeded("Bookmark metadata exceeds the response budget.")
            entries.pop()

    def open(self, name, *, budget):
        _name(name)
        row = self._visible(name)
        bookmark = _view(row)
        if bookmark["state"] == "stale":
            raise StaleSnapshot("Bookmark pins an older task snapshot. Choose a new checkpoint explicitly.")
        if bookmark["state"] == "unavailable":
            raise NotFound("The checkpoint pinned by this bookmark is unavailable.")
        scope = Scope(**bookmark["scope"])
        navigation = TaskNavigation(self.store, replace(self.config, scope=scope), self.session)
        packet = navigation.resume(bookmark["snapshot"], budget=budget)
        search = None
        if bookmark["query"] is not None:
            try:
                search = self.store.search_indexed(scope, bookmark["query"], limit=5, budget=self.config.response_bytes)
            except (Unavailable, InvalidRequest):
                search = navigation.search(bookmark["query"], limit=5)
        with self.store.transaction():
            current = self._visible(name)
            if (current["scope"], current["snapshot"], current["created"]) != (row["scope"], row["snapshot"], row["created"]):
                raise Conflict("Bookmark changed while opening.")
            if _view(current)["state"] != "store_current":
                raise StaleSnapshot("The pinned task changed while opening.")
            now = self.store.clock()
            self.store.db.execute("UPDATE task_bookmarks SET last_opened=? WHERE namespace=? AND name=?",
                                  (now, self.namespace, name))
        return {"bookmark": {**bookmark, "last_opened_at": now}, "packet": packet, "search": search}

    def remove(self, name, lease):
        _name(name)
        with self.store.transaction():
            self.store._lease(self.scope, lease)
            row = self._visible(name)
            if row["scope"] != self.scope.key:
                raise NotFound("Choose the task pinned by this bookmark before removing it.")
            self.store.db.execute("DELETE FROM task_bookmarks WHERE namespace=? AND name=?", (self.namespace, name))
        return {"removed": name, "scope": asdict(self.scope)}
