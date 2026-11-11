"""Versioned web observations supplied by a trusted local capture host."""

from contextlib import nullcontext
from dataclasses import asdict
import json
import math
from urllib.parse import urlsplit, urlunsplit

from .errors import BudgetExceeded, Conflict, InvalidRequest
from .models import Ref, canonical, identifier, integer, unicode_text


_SCHEMA = """CREATE TABLE IF NOT EXISTS web_cache_entries(
    scope TEXT NOT NULL REFERENCES tasks(scope), source TEXT NOT NULL, url TEXT NOT NULL,
    revision INTEGER NOT NULL, fetch_id TEXT NOT NULL, observation TEXT NOT NULL, response TEXT NOT NULL,
    PRIMARY KEY(scope,source,url,revision), UNIQUE(scope,source,url,fetch_id))"""
_REQUIRED = {"source", "url", "status", "fetched_at", "ttl_seconds", "expected_revision", "fetch_id"}
_OPTIONAL = {"final_url", "http_version", "etag", "last_modified", "validator", "ref"}


def _text(value, limit):
    if not isinstance(value, str) or not value or len(value) > limit or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise InvalidRequest("Web observation field must be bounded single-line text.")
    unicode_text(value)
    return value


def _url(value):
    value = _text(value, 2048)
    if any(char.isspace() for char in value) or "\\" in value:
        raise InvalidRequest("Use an absolute HTTP(S) URL without whitespace or backslashes.")
    try:
        parts = urlsplit(value)
        if parts.scheme not in {"https", "http"} or not parts.hostname or parts.username is not None or parts.password is not None:
            raise ValueError()
        host = parts.hostname.encode("idna").decode("ascii").lower()
        if ":" in host:
            host = f"[{host}]"
        port = parts.port
        if port is not None and port != (443 if parts.scheme == "https" else 80):
            host += ":" + str(port)
        return urlunsplit((parts.scheme, host, parts.path or "/", parts.query, ""))
    except (ValueError, UnicodeError):
        raise InvalidRequest("Use an absolute HTTP(S) URL without embedded credentials.") from None


def _observation(data, now):
    if not isinstance(data, dict) or _REQUIRED - data.keys() or data.keys() - _REQUIRED - _OPTIONAL:
        raise InvalidRequest("Provide a bounded web response observation.")
    value = dict(data)
    identifier(value["source"])
    identifier(value["fetch_id"])
    value["url"] = _url(value["url"])
    if "final_url" in value:
        value["final_url"] = _url(value["final_url"])
    for field in ("etag", "last_modified", "http_version"):
        if field in value:
            value[field] = _text(value[field], 256 if field != "http_version" else 16)
    status = value["status"]
    if type(status) is not int or status not in {200, 304}:
        raise InvalidRequest("Only an observed HTTP 200 body or 304 revalidation may update the cache.")
    ttl = value["ttl_seconds"]
    integer(ttl, 1)
    integer(value["expected_revision"])
    fetched = value["fetched_at"]
    if (ttl > 31_536_000 or type(fetched) not in (int, float) or not 0 <= fetched <= now + 1
            or not math.isfinite(fetched)):
        raise InvalidRequest("Fetch time must be observed epoch seconds and TTL at most one year.")
    if status == 200:
        if not isinstance(value.get("ref"), dict) or "validator" in value:
            raise InvalidRequest("HTTP 200 requires the saved original response body Ref.")
        try:
            ref = Ref(**value["ref"])
        except TypeError:
            raise InvalidRequest("Invalid web response body Ref.") from None
        if ref.start != 0 or ref.end is not None:
            raise InvalidRequest("A web response observation requires the complete saved body.")
        value["ref"] = asdict(ref)
    else:
        validator = value.get("validator")
        if (not isinstance(validator, dict) or len(validator) != 1
                or validator.keys() - {"if_none_match", "if_modified_since"} or "ref" in value):
            raise InvalidRequest("HTTP 304 requires the exact conditional validator used and no new body.")
        value["validator"] = {key: _text(header, 256) for key, header in validator.items()}
    return value


def _available(store, scope, ref, now):
    return store.db.execute(
        "SELECT 1 FROM artifacts a JOIN blobs b ON b.scope=a.scope AND b.sha256=a.sha256 "
        "WHERE a.scope=? AND a.name=? AND a.revision=? AND a.invalidated=0 AND (a.expires IS NULL OR a.expires>?)",
        (scope.key, ref["name"], ref["revision"], now),
    ).fetchone() is not None


def _view(store, scope, response, *, refresh, current, budget):
    now = store.clock()
    value = {**response}
    ref = value.pop("ref")
    value["schema"] = "contextrail.web-cache.v1"
    available = _available(store, scope, ref, now)
    value["state"] = ("superseded" if not current else "unavailable" if not available
                      else "refresh_required" if refresh else "stale" if now >= value["expires_at"] else "fresh")
    if value["state"] == "fresh":
        value["read_ref"] = ref
    elif available:
        value["historical_ref"] = ref
    if value["state"] in {"stale", "refresh_required"}:
        if value.get("etag"):
            value["conditional"] = {"if_none_match": value["etag"]}
        elif value.get("last_modified"):
            value["conditional"] = {"if_modified_since": value["last_modified"]}
    integer(budget, 1)
    if len(canonical(value).encode("utf-8")) > budget:
        raise BudgetExceeded("Web cache metadata exceeds the response budget.")
    return value


def _latest(store, key):
    return store.db.execute(
        "SELECT revision,response FROM web_cache_entries WHERE scope=? AND source=? AND url=? "
        "ORDER BY revision DESC LIMIT 1", key,
    ).fetchone()


def save_observation(store, scope, lease, data, *, budget):
    observation = _observation(data, store.clock())
    key = (scope.key, observation["source"], observation["url"])
    serialized = canonical(observation)
    with store.transaction():
        store._lease(scope, lease)
        store.db.execute(_SCHEMA)
        row = _latest(store, key)
        head = row["revision"] if row else 0
        replay = store.db.execute(
            "SELECT revision,observation,response FROM web_cache_entries WHERE scope=? AND source=? AND url=? AND fetch_id=?",
            (*key, observation["fetch_id"]),
        ).fetchone()
        if replay:
            if replay["observation"] != serialized:
                raise Conflict("The fetch ID already records a different observation for this source and URL.")
            return _view(store, scope, json.loads(replay["response"]), refresh=False,
                         current=replay["revision"] == head, budget=budget)
        if head != observation["expected_revision"]:
            raise Conflict("Web cache revision changed. Read the current source before updating it.")
        previous = json.loads(row["response"]) if row else {}
        fetched = observation["fetched_at"]
        if previous and fetched < previous["fetched_at"]:
            raise Conflict("An older web observation cannot replace a later one.")
        response = {key: observation[key] for key in ("source", "url", "status", "fetched_at", "fetch_id")}
        response.update(revision=head + 1, expires_at=fetched + observation["ttl_seconds"])
        if observation["status"] == 304:
            expected = ({"if_none_match": previous["etag"]} if previous.get("etag") else
                        {"if_modified_since": previous["last_modified"]} if previous.get("last_modified") else {})
            if (observation["validator"] != expected or not previous or
                    observation.get("final_url", previous["final_url"]) != previous["final_url"] or
                    not _available(store, scope, previous["ref"], store.clock())):
                raise Conflict("HTTP 304 did not validate the current saved representation. Fetch a new body.")
            response.update({key: previous[key] for key in ("ref", "final_url", "etag", "last_modified", "http_version") if key in previous})
        else:
            store.get(scope, Ref(**observation["ref"]))
            response.update(ref=observation["ref"], final_url=observation.get("final_url", observation["url"]))
        response.update({key: observation[key] for key in ("etag", "last_modified", "http_version") if key in observation})
        store.db.execute("INSERT INTO web_cache_entries VALUES(?,?,?,?,?,?,?)",
                         (*key, head + 1, observation["fetch_id"], serialized, canonical(response)))
        return _view(store, scope, response, refresh=False, current=True, budget=budget)


def lookup(store, scope, *, source, url, refresh=False, budget):
    identifier(source)
    if type(refresh) is not bool:
        raise InvalidRequest("Web cache refresh must be boolean.")
    url = _url(url)
    with (nullcontext() if store.db.in_transaction else store.read_transaction()):
        table = store.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='web_cache_entries'").fetchone()
        row = _latest(store, (scope.key, source, url)) if table else None
        if row:
            return _view(store, scope, json.loads(row["response"]), refresh=refresh, current=True, budget=budget)
        result = {"schema": "contextrail.web-cache.v1", "state": "miss", "source": source, "url": url, "revision": 0}
        integer(budget, 1)
        if len(canonical(result).encode("utf-8")) > budget:
            raise BudgetExceeded("Web cache metadata exceeds the response budget.")
        return result
