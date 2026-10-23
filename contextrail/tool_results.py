"""Receive host-observed results, preserve bytes and produce a bounded projection.

This module does not execute commands. The caller owns capture and execution authority.
"""

import base64
import binascii
from dataclasses import asdict, dataclass
from fnmatch import fnmatchcase
import math
import os
import sqlite3
from pathlib import Path

from .errors import AccessDenied, BudgetExceeded, Conflict, InvalidRequest, NotFound, RailError, Unavailable
from .models import Lease, Ref, Scope, canonical, identifier, integer, unicode_text
from .store import Store


@dataclass(frozen=True)
class ObservedResult:
    metadata: dict
    stdout: bytes
    stderr: bytes

    @classmethod
    def parse(cls, data, workspace, *, ignore=()):
        required = {"call_id", "tool", "argv", "cwd", "started_at", "finished_at", "exit_code"}
        optional = {"stdout", "stderr", "stdout_base64", "stderr_base64", "timed_out", "partial", "source_paths", "environment"}
        if not isinstance(data, dict) or not required <= data.keys() or data.keys() - required - optional:
            raise InvalidRequest("Missing or unsupported observed-result fields.")
        for key in ("call_id", "tool"):
            identifier(data[key])
        if len(data["call_id"]) > 128:
            raise InvalidRequest("Call ID cannot exceed 128 characters.")
        if not isinstance(data["argv"], list) or any(not isinstance(arg, str) for arg in data["argv"]):
            raise InvalidRequest("argv must be an array of strings.")
        for arg in data["argv"]:
            unicode_text(arg)
        if not isinstance(data["cwd"], str) or not data["cwd"] or "\x00" in data["cwd"]:
            raise InvalidRequest("cwd must be an absolute workspace path.")
        cwd = Path(data["cwd"])
        if not cwd.is_absolute():
            raise InvalidRequest("cwd must be absolute.")
        cwd = cwd.resolve()
        if not cwd.is_relative_to(Path(workspace).resolve()):
            raise AccessDenied("Observed result cwd is outside the configured workspace.")
        sources = data.get("source_paths", [])
        if not isinstance(sources, list):
            raise InvalidRequest("source_paths must be a list of workspace-relative paths.")
        candidates = [cwd]
        for source in sources:
            if not isinstance(source, str) or not source or "\x00" in source or Path(source).is_absolute():
                raise InvalidRequest("Source paths must be relative to the configured workspace.")
            resolved = (Path(workspace) / source).resolve()
            if not resolved.is_relative_to(Path(workspace).resolve()):
                raise AccessDenied("Source path is outside the configured workspace.")
            candidates.append(resolved)
        for path in candidates:
            relative = path.relative_to(Path(workspace).resolve())
            ancestors = [relative, *relative.parents]
            names = [str(item).replace("\\", "/") for item in ancestors]
            # Like Windows filesystem paths, ignore comparisons are case-insensitive there.
            normalize = str.casefold if os.name == "nt" else lambda value: value
            if any(fnmatchcase(normalize(name), normalize(pattern.replace("\\", "/"))) or
                   fnmatchcase(normalize(name + "/"), normalize(pattern.replace("\\", "/")))
                   for name in names for pattern in ignore):
                raise AccessDenied("Observed result is excluded by the project's capture rules.")
        for key in ("started_at", "finished_at"):
            try:
                valid_time = type(data[key]) in (int, float) and math.isfinite(data[key]) and data[key] >= 0
            except OverflowError:
                valid_time = False
            if not valid_time:
                raise InvalidRequest("Execution times must be finite nonnegative timestamps.")
        if data["finished_at"] < data["started_at"]:
            raise InvalidRequest("Finish precedes start.")
        if data["exit_code"] is not None and (type(data["exit_code"]) is not int or not -(2**63) <= data["exit_code"] < 2**63):
            raise InvalidRequest("Exit code must be an integer or null.")
        for key in ("timed_out", "partial"):
            if type(data.get(key, False)) is not bool:
                raise InvalidRequest("Timeout and partial flags must be boolean.")
        def stream(key):
            encoded = key + "_base64"
            if (key in data) == (encoded in data):
                raise InvalidRequest("Supply each stream as either text or base64, exactly once.")
            try:
                if encoded in data:
                    if not isinstance(data[encoded], str):
                        raise ValueError()
                    return base64.b64decode(data[encoded], validate=True)
                if not isinstance(data[key], str):
                    raise ValueError()
                return data[key].encode("utf-8")
            except (ValueError, binascii.Error, UnicodeError):
                raise InvalidRequest("Invalid output encoding.") from None
        metadata = {key: data[key] for key in required}
        metadata.update(cwd=str(cwd), source_paths=sources,
                        timed_out=data.get("timed_out", False), partial=data.get("partial", False))
        if 'environment' in data:
            environment=data['environment'];allowed={'executable','virtual_env','prefix','base_prefix','worktree','host_executable','host_prefix','host_base_prefix'}
            if not isinstance(environment,dict) or environment.keys()-allowed or any(not isinstance(v,str) or len(v)>4096 for v in environment.values()):raise InvalidRequest('Invalid host-attested environment')
            from .sanitization import sanitize_capture
            metadata['environment']=sanitize_capture(environment,'evidence').value
        return cls(metadata, stream("stdout"), stream("stderr"))

    def status(self):
        meta = self.metadata
        if meta["timed_out"]:
            return "timed_out"
        if meta["partial"]:
            return "partial"
        if meta["exit_code"] is None:
            return "unknown"
        if meta["exit_code"] == 0:
            return "succeeded"
        argv = meta["argv"]
        executable = argv[0].replace("\\", "/").rsplit("/", 1)[-1].lower() if argv else ""
        if (meta["tool"] == "shell" and executable in {"rg", "rg.exe", "grep", "grep.exe"}
                and meta["exit_code"] == 1 and not self.stdout and not self.stderr):
            return "no_match"
        return "failed"


def _stream(content, ref):
    try:
        text, encoding = content.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        text, encoding = "", "binary"
    return {"ref": asdict(ref), "bytes": len(content), "encoding": encoding,
            "preview": "", "truncated": bool(content)}, text


def project_result(result, refs, budget, *, previews=True):
    """Budget the complete UTF-8 JSON document; preview is always optional."""
    integer(budget, 1)
    stdout, out = _stream(result.stdout, refs[0])
    stderr, err = _stream(result.stderr, refs[1])
    meta = result.metadata
    payload = {"schema": "contextrail.tool-result.v1", "call_id": meta["call_id"], "tool": meta["tool"],
               "status": result.status(), "exit_code": meta["exit_code"], "timed_out": meta["timed_out"],
               "partial": meta["partial"], "result_ref": asdict(refs[2]), "stdout": stdout, "stderr": stderr,
               "read_tool": "context.get", "range_unit": "bytes", "trust": "untrusted_evidence",
               "projection": "head_tail_preview; full streams preserved"}
    if len(canonical(payload).encode("utf-8")) > budget:
        raise BudgetExceeded("Response budget cannot contain required result references; increase response_bytes.")
    if not previews:
        return payload
    def preview(text, count):
        if count >= len(text):
            return text
        if count < 3:
            return ""
        head = (count - 1) // 2
        tail = count - 1 - head
        return text[:head] + "…" + text[-tail:]
    low, high = 0, max(len(out), len(err))
    while low < high:
        count = (low + high + 1) // 2
        for stream, text in ((stdout, out), (stderr, err)):
            stream["preview"] = preview(text, count)
            stream["truncated"] = stream["encoding"] == "binary" and bool(stream["bytes"]) or len(text) > count
        if len(canonical(payload).encode("utf-8")) <= budget:
            low = count
        else:
            high = count - 1
    for stream, text in ((stdout, out), (stderr, err)):
        stream["preview"] = preview(text, low)
        stream["truncated"] = (stream["encoding"] == "binary" and bool(stream["bytes"])) or len(text) > low
    return payload


class OutputRecorder:
    def __init__(self, store: Store, scope: Scope, lease: Lease, workspace, *, ignore=()):
        self.store, self.scope, self.lease, self.workspace = store, scope, lease, workspace
        self.ignore = tuple(ignore)

    def capture(self, data, *, budget=4096, intent=None):
        result = ObservedResult.parse(data, self.workspace, ignore=self.ignore)
        prefix = "tool-result/" + result.metadata["call_id"]
        refs = tuple(Ref(prefix + "/" + suffix, 1) for suffix in ("stdout", "stderr", "result"))
        # Validate the display contract before committing, so callers can safely retry.
        projection = project_result(result, refs, budget, previews=intent is None)
        if intent is not None:
            from .result_retrieval import validate_query, compact_intent
            validate_query(intent)
            projection["projection"] = "intent_ranked_original_spans; full streams preserved"
            projection["intent"] = compact_intent(intent, reserve=True)
            if len(canonical(projection).encode("utf-8")) > budget:
                raise BudgetExceeded("Response budget cannot preserve execution, original references and required intent metadata.")
        manifest = dict(schema="contextrail.tool-result.v1", source_session=self.lease.session,
                        scope=asdict(self.scope), status=result.status(), **result.metadata,
                        stdout={"ref": asdict(refs[0]), "bytes": len(result.stdout)},
                        stderr={"ref": asdict(refs[1]), "bytes": len(result.stderr)})
        bodies = (result.stdout, result.stderr, canonical(manifest).encode("utf-8"))
        items = [{"name": ref.name, "content": body, "expected_revision": 0,
                  "media_type": "application/json" if index == 2 else "application/octet-stream"}
                 for index, (ref, body) in enumerate(zip(refs, bodies))]
        try:
            self.store.put_many(self.scope, self.lease, items)
        except Conflict:
            # put_many fences before conflicts; fence again during the retry read.
            with self.store.read_transaction():
                self.store._lease(self.scope, self.lease)
                try:
                    identical = all(self.store.get(self.scope, ref).content == body for ref, body in zip(refs, bodies))
                except NotFound:
                    identical = False
                if not identical:
                    raise Conflict("Call ID already exists with different or incomplete result data.") from None
        if intent is not None:
            from .result_retrieval import search_result, compact_intent
            try:
                body = search_result(self.store, self.scope, self.lease.session, asdict(refs[2]), intent, budget=16384)
                projection["intent"] = compact_intent(intent, body)
            except RailError as exc:
                projection["intent"] = compact_intent(intent, error=exc)
            except sqlite3.Error:
                projection["intent"] = compact_intent(intent, error=Unavailable("Optional index retrieval failed; original result references remain available."))
            summary = projection["intent"]
            while len(canonical(projection).encode("utf-8")) > budget:
                if summary["searchable_terms"]:
                    summary["searchable_terms"].pop()
                elif summary["hits"]:
                    summary["hits"].pop()
                    summary["returned"] = len(summary["hits"])
                    summary["omitted"] = summary["total"] - summary["returned"]
                else:
                    raise BudgetExceeded("Required intent metadata exceeded its preflight reservation.")
        from .accounting import record_capture
        record_capture(self.store,self.scope,self.lease.session,result,projection)
        from .workscene import WorkScene
        WorkScene(self.store,self.scope,self.lease,self.workspace).host_result(refs[2])
        return projection
