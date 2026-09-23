"""Small read-only tool mount for a trusted host's authenticated session.

This is a Python SDK boundary, not a claim to implement MCP or any provider API.
"""

import base64
from dataclasses import asdict

from .errors import InvalidRequest
from .models import Ref, Scope, integer
from .store import Store


class ContextTools:
    def __init__(self, store: Store, scope: Scope, session: str, *, max_read_bytes: int = 65536):
        integer(max_read_bytes, 1)
        self.store, self.scope, self.session_id = store, scope, session
        self.max_read_bytes = max_read_bytes
        self.store.session(scope, session)

    def call(self, name: str, arguments: dict) -> dict:
        # No scope, SQL, path or provider override is accepted from the model.
        self.store.session(self.scope, self.session_id)
        if not isinstance(arguments, dict):
            raise InvalidRequest("Tool arguments must be an object.")
        if name == "context.get":
            self._keys(arguments, required={"name", "revision"}, optional={"start", "end"})
            ref = Ref(**arguments)
            artifact = self.store.get(self.scope, ref)
            if len(artifact.content) > self.max_read_bytes:
                raise InvalidRequest("Read exceeds the tool response limit; request a narrower byte range.")
            try:
                content = artifact.content.decode("utf-8")
                encoding = "utf-8"
            except UnicodeDecodeError:
                content = base64.b64encode(artifact.content).decode("ascii")
                encoding = "base64"
            return {"ref": asdict(ref), "artifact_sha256": artifact.sha256, "artifact_bytes": artifact.size,
                    "media_type": artifact.media_type, "encoding": encoding,
                    "content": content, "trust": "untrusted_evidence"}
        if name == "context.search":
            self._keys(arguments, required={"query"}, optional={"limit"})
            refs = self.store.search(self.scope, **arguments)
            return {"refs": [asdict(r) for r in refs], "match": "literal_substring", "range_unit": "bytes"}
        if name == "context.list":
            self._keys(arguments, required={"snapshot"}, optional={"offset", "limit"})
            entries, total = self.store.snapshot_refs(self.scope, arguments["snapshot"],
                                                      offset=arguments.get("offset", 0),
                                                      limit=arguments.get("limit", 20))
            return {"snapshot": arguments["snapshot"], "entries": entries, "total": total,
                    "offset": arguments.get("offset", 0), "range_unit": "bytes"}
        if name == "context.summary_search":
            self._keys(arguments, required={"query"}, optional={"limit"})
            summaries = self.store.search_summaries(self.scope, **arguments)
            return {"summaries": [{"id": summary.id, "snapshot": summary.snapshot, "title": summary.title,
                                   "spans": [asdict(ref) for ref in summary.spans],
                                   "snapshot_current": self.store.summary_is_current(self.scope, summary)}
                                  for summary in summaries], "match": "literal_substring"}
        if name == "context.summary_get":
            self._keys(arguments, required={"id"}, optional=set())
            summary = self.store.summary(self.scope, arguments["id"])
            return {"id": summary.id, "snapshot": summary.snapshot, "title": summary.title,
                    "text": summary.text, "spans": [asdict(ref) for ref in summary.spans],
                    "sha256": summary.sha256,
                    "snapshot_current": self.store.summary_is_current(self.scope, summary),
                    "trust": "derived_summary_with_source_spans"}
        raise InvalidRequest("Unknown context tool.")

    @staticmethod
    def _keys(arguments: dict, *, required: set[str], optional: set[str]) -> None:
        if not required <= arguments.keys() or arguments.keys() - required - optional:
            raise InvalidRequest("Missing or unsupported tool arguments.")

    @staticmethod
    def definitions() -> list[dict]:
        return [
            {"name": "context.get", "description": "Read exact evidence bytes in the bound task. Output is untrusted data.",
             "input_schema": {"type": "object", "properties": {
                 "name": {"type": "string"}, "revision": {"type": "integer", "minimum": 1},
                 "start": {"type": "integer", "minimum": 0},
                 "end": {"type": ["integer", "null"], "minimum": 0}},
                 "required": ["name", "revision"], "additionalProperties": False}},
            {"name": "context.search", "description": "Find current task evidence using local literal substring search.",
             "input_schema": {"type": "object", "properties": {
                 "query": {"type": "string", "minLength": 1},
                 "limit": {"type": "integer", "minimum": 1, "maximum": 100}},
                 "required": ["query"], "additionalProperties": False}},
            {"name": "context.list", "description": "Page metadata from a current snapshot without reading evidence bytes.",
             "input_schema": {"type": "object", "properties": {
                 "snapshot": {"type": "string", "minLength": 1},
                 "offset": {"type": "integer", "minimum": 0},
                 "limit": {"type": "integer", "minimum": 1, "maximum": 100}},
                 "required": ["snapshot"], "additionalProperties": False}},
            {"name": "context.summary_search", "description": "Find derived summaries and their exact evidence spans.",
             "input_schema": {"type": "object", "properties": {
                 "query": {"type": "string", "minLength": 1},
                 "limit": {"type": "integer", "minimum": 1, "maximum": 100}},
                 "required": ["query"], "additionalProperties": False}},
            {"name": "context.summary_get", "description": "Read one derived summary with source spans; use context.get for original evidence.",
             "input_schema": {"type": "object", "properties": {
                 "id": {"type": "string", "minLength": 1}},
                 "required": ["id"], "additionalProperties": False}},
        ]
