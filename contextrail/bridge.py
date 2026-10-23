"""One-way, source-checked Bridge requirement evidence for a bound local task.

The installed Bridge validator remains authoritative. Nothing in this adapter
writes the source bundle, attests alignment for a receiver, or acquires ownership.
"""

from dataclasses import asdict, dataclass
import json
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tempfile
import uuid

from .context import Compiler
from .errors import AccessDenied, BudgetExceeded, Conflict, InvalidRequest, NotFound, StaleSnapshot, Unavailable
from .models import IndexPolicy, Lease, Ref, Scope, Selection, canonical, identifier, integer
from .store import Store


MAX_BYTES = 64 * 1024 * 1024
MAX_LEDGER = 2 * 1024 * 1024
MAX_FILES = 1024
MANIFEST_TYPE = "application/vnd.contextrail.bridge-manifest+json"


def _json(raw):
    def invalid_constant(value):
        raise InvalidRequest("Bridge JSON must not contain non-finite numbers.")
    try:
        return json.loads(raw, parse_constant=invalid_constant)
    except (ValueError, UnicodeError, RecursionError):
        raise InvalidRequest("Invalid UTF-8 Bridge JSON.") from None


def _read(path, limit):
    with Path(path).open("rb") as stream:
        content = stream.read(limit + 1)
    if len(content) > limit:
        raise InvalidRequest("Bridge capture exceeds its source byte limit.")
    return content


def _inside(root, name):
    if not isinstance(name, str) or not name or "\\" in name or ":" in name or "\x00" in name:
        raise InvalidRequest("Evidence requires a bundle-relative POSIX path.")
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts:
        raise AccessDenied("Bridge evidence cannot leave its bundle.")
    resolved = (root / name).resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise AccessDenied("Bridge evidence resolves outside its bundle.")
    return resolved


@dataclass(frozen=True)
class CapturedBundle:
    root: Path
    raw: bytes
    ledger: dict
    sources: dict[str, bytes]

    @classmethod
    def capture(cls, root):
        root = Path(root).resolve()
        raw = _read(_inside(root, "requirements.json"), MAX_LEDGER)
        data = _json(raw)
        if not isinstance(data, dict) or not isinstance(data.get("requirements"), list):
            raise InvalidRequest("A Bridge bundle needs a requirements array.")
        names = set()
        for row in data["requirements"]:
            if not isinstance(row, dict) or not isinstance(row.get("source"), dict) or not isinstance(row.get("evidence"), list):
                raise InvalidRequest("Bridge rows require a source and evidence list.")
            paths = [row["source"].get("path"), *row["evidence"]]
            for name in paths:
                _inside(root, name)
                names.add(name)
        if len(names) > MAX_FILES:
            raise InvalidRequest("Bridge capture exceeds its source file limit.")
        sources, used = {}, len(raw)
        for name in sorted(names):
            sources[name] = _read(_inside(root, name), MAX_BYTES - used)
            used += len(sources[name])
        return cls(root, raw, data, sources)

    def materialize(self, root):
        root.mkdir(parents=True, exist_ok=True)
        (root / "requirements.json").write_bytes(self.raw)
        for name, content in self.sources.items():
            path = _inside(root, name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)


class BridgeValidator:
    """Trusted-host script path; never a model tool argument."""
    def __init__(self, script=None):
        self.script = Path(script) if script else Path.home() / ".codex/skills/context-bridge/scripts/requirements.py"

    def call(self, action, bundle: CapturedBundle, *, previous=None, scope=None, receipt=None):
        if not self.script.is_file():
            raise Unavailable("Install context-bridge or provide --bridge-validator with its requirements.py.")
        with tempfile.TemporaryDirectory(prefix="contextrail-bridge-source-") as folder:
            home = Path(folder)
            bundle.materialize(home / "bundle")
            args = [sys.executable, str(self.script.resolve()), action, str(home / "bundle")]
            if previous is not None:
                previous.materialize(home / "previous")
                args += ["--previous", str(home / "previous")]
            if scope is not None:
                args += ["--scope", scope]
            if receipt is not None:
                (home / "receipt.json").write_bytes(receipt)
                args += ["--receipt", str(home / "receipt.json"), "--snapshot-id", bundle.ledger["snapshot_id"]]
            try:
                completed = subprocess.run(args, capture_output=True, timeout=30)
            except (OSError, subprocess.TimeoutExpired):
                raise Unavailable("Bridge authority could not complete validation.") from None
            if completed.returncode:
                try:
                    reason = _json(completed.stderr).get("error", "Bridge rejected the bundle.")
                except (InvalidRequest, AttributeError):
                    reason = "Bridge authority could not validate this bundle."
                raise InvalidRequest(str(reason))
            result = _json(completed.stdout)
            if not isinstance(result, dict):
                raise Unavailable("Bridge validator returned an unsupported result.")
            return result


class BridgeEvidence:
    def __init__(self, store: Store, scope: Scope, session: str, *, validator=None):
        self.store, self.scope, self.session = store, scope, session
        self.validator = BridgeValidator(validator)
        store.session(scope, session)
        self.prefix = "bridge/" + uuid.uuid5(uuid.NAMESPACE_URL, scope.task).hex

    def _head(self, name):
        row = self.store.db.execute("SELECT revision FROM heads WHERE scope=? AND name=?", (self.scope.key, name)).fetchone()
        return Ref(name, row[0]) if row else None

    def _manifest(self, ref):
        value = _json(self.store.get(self.scope, ref).content)
        if not isinstance(value, dict) or value.get("schema") != "contextrail.bridge/v1" or value.get("task_id") != self.scope.task:
            raise InvalidRequest("Not a Bridge export for this task.")
        return value

    def _bundle(self, manifest):
        raw = self.store.get(self.scope, Ref(**manifest["ledger_ref"])).content
        sources = {name: self.store.get(self.scope, Ref(**ref)).content for name, ref in manifest["sources"].items()}
        return CapturedBundle(Path(manifest["bundle_path"]), raw, _json(raw), sources)

    def _publication(self, manifest_ref):
        for row in self.store.db.execute("SELECT metadata FROM events WHERE scope=? AND kind='context.published' ORDER BY sequence DESC",
                                         (self.scope.key,)):
            event = _json(row["metadata"])
            if event.get("source_ref") == asdict(manifest_ref):
                return event["snapshot"]
        raise NotFound("Bridge publication was not committed.")

    def import_bundle(self, root, bridge_scope, lease: Lease):
        identifier(bridge_scope)
        if lease.session != self.session:
            raise AccessDenied("Export lease must belong to the bound session.")
        with self.store.read_transaction():
            self.store._lease(self.scope, lease)
            runtime_task = self.store.task(self.scope)
            version = runtime_task["version"]
            current_ref = self._head(self.prefix + "/current")
            previous_manifest = self._manifest(current_ref) if current_ref else None
            previous = self._bundle(previous_manifest) if previous_manifest else None
            baseline = previous_manifest["base_requirements"] if previous_manifest else {
                key: runtime_task[key] for key in ("objective", "constraints", "acceptance")}
            if previous_manifest:
                prior_rows = [row for row in previous.ledger["requirements"] if row["status"] == "active" and
                              (row["always_load"] or row["scope"] == previous_manifest["bridge_scope"])]
                derived = {"constraints": {row["statement"] for row in prior_rows if row["kind"] == "constraint"},
                           "acceptance": {row["acceptance"] for row in prior_rows}}
                # Keep later host edits too; only the prior Bridge contribution
                # is replaced. Values with both provenances remain host-pinned.
                baseline = {"objective": baseline["objective"], **{
                    key: [value for value in runtime_task[key] if value not in derived[key] or value in baseline[key]]
                    for key in ("constraints", "acceptance")}}
        bundle = CapturedBundle.capture(root)
        data = bundle.ledger
        if data.get("task_id") != self.scope.task:
            raise AccessDenied("Bridge task_id differs from the bound Rail task; choose its explicit --task.")
        snapshot = data.get("snapshot_id")
        identifier(snapshot)
        if not snapshot.startswith(self.scope.task + "/") or snapshot == self.scope.task + "/":
            raise InvalidRequest("Bridge snapshot_id must have an explicit task namespace.")
        identity = uuid.uuid5(uuid.NAMESPACE_URL, canonical([snapshot, bridge_scope])).hex
        prefix = self.prefix + "/exports/" + identity
        manifest_ref = Ref(prefix + "/manifest", 1)
        existing = self._head(manifest_ref.name)
        if existing:
            old = self._manifest(existing)
            frozen = self._bundle(old)
            if frozen.raw != bundle.raw or frozen.sources != bundle.sources or frozen.root != bundle.root:
                raise Conflict("Bridge checkpoint identity was reused with different evidence.")
            sid = self._publication(existing)
            return self._describe(old, existing, sid)
        if previous is not None and previous.ledger["snapshot_id"] == snapshot and (
                previous.raw != bundle.raw or previous.sources != bundle.sources):
            raise Conflict("Bridge checkpoint identity was reused in another scope with different evidence.")
        # All semantic validation uses the same frozen bytes we will publish.
        self.validator.call("check", bundle, previous=previous)
        brief = self.validator.call("brief", bundle, scope=bridge_scope)
        from .intent import declared, publish
        anchors=declared(bundle)
        chosen = {row["id"] for row in brief["requirements"]}
        rows = data["requirements"]
        for row in rows:
            identifier(row["id"])
            integer(row["revision"], 1)
        items, selections = [], []
        def artifact(name, content, *, media="application/json", required=False, priority=0, selected=True):
            ref = Ref(name, 1)
            items.append({"name": name, "content": content, "expected_revision": 0, "media_type": media})
            if selected:
                selections.append(Selection(ref, required=required, priority=priority))
            return asdict(ref)
        ledger_ref = artifact(prefix + "/ledger", bundle.raw)
        sources = {name: artifact(prefix + "/sources/" + uuid.uuid5(uuid.NAMESPACE_URL, name).hex, content,
                                   media="application/octet-stream") for name, content in bundle.sources.items()}
        intent=publish(bundle,anchors,sources,prefix,artifact,selections)
        if anchors:
            chosen.update(a['requirement_id'] for role,a in anchors.items() if role!='original_goal' or a['status']=='active')
        intent_ids={a['requirement_id'] for a in anchors.values()} if anchors else set()
        requirements, required_ids = {}, []
        active = [row for row in rows if row["id"] in chosen]
        for row in rows:
            mandatory = row["id"] in chosen and (row["always_load"] or row["kind"] in {"goal", "constraint"} or row['id'] in intent_ids)
            ref = artifact(prefix + "/requirements/" + row["id"], canonical(row).encode("utf-8"),
                           required=mandatory, priority=100 if mandatory else 10, selected=row["id"] in chosen)
            requirements[row["id"]] = {"ref": ref, "revision": row["revision"], "status": row["status"],
                                       "scope": row["scope"], "required": mandatory}
            if mandatory:
                required_ids.append(row["id"])
        manifest = {"schema": "contextrail.bridge/v1", "task_id": data["task_id"], "snapshot_id": snapshot,
                    "bridge_scope": bridge_scope, "bundle_path": str(bundle.root), "ledger_ref": ledger_ref,
                    "sources": sources, "requirements": requirements, "required_ids": sorted(required_ids),
                    "base_requirements": baseline,
                    "deferred_count": sum(row['status']=='active' for row in rows)-len(active)}
        if intent is not None:manifest['resume_intent']=intent
        raw_manifest = canonical(manifest).encode("utf-8")
        artifact(manifest_ref.name, raw_manifest, media=MANIFEST_TYPE)
        items.append({"name": self.prefix + "/current", "content": raw_manifest,
                      "expected_revision": current_ref.revision if current_ref else 0, "media_type": MANIFEST_TYPE})
        objective = "\n".join(row["statement"] for row in active if row["kind"] == "goal") or "Bridge task: " + self.scope.task
        constraints = tuple(dict.fromkeys([*baseline["constraints"],
                                          *(row["statement"] for row in active if row["kind"] == "constraint")]))
        acceptance = tuple(dict.fromkeys([*baseline["acceptance"], *(row["acceptance"] for row in active)]))
        sid = self.store.publish_context(self.scope, lease, items, tuple(selections), objective=objective,
                                         constraints=constraints, acceptance=acceptance, expected_version=version,
                                         source_ref=manifest_ref)
        return self._describe(manifest, manifest_ref, sid)

    def _describe(self, manifest, ref, sid):
        from .learning_origin import origin
        from .intent import UNKNOWN
        current = self._head(self.prefix + "/current")
        export_current = current is not None and self.store.get(self.scope, current).content == self.store.get(self.scope, ref).content
        return {"task_id": manifest["task_id"], "bridge_snapshot": manifest["snapshot_id"], "snapshot": sid,
                "bridge_scope": manifest["bridge_scope"], "manifest_ref": asdict(ref),
                "required_ids": manifest["required_ids"], "deferred_count": manifest["deferred_count"],
                "resume_intent": manifest.get('resume_intent',dict(UNKNOWN)),
                "export_current": export_current,
                "origin": origin('bridge:'+manifest['task_id']+':'+manifest['snapshot_id'],[asdict(ref)],'bridge_projection',False),
                "trust": "derived_from_bridge_source"}

    def _resolve(self, sid):
        self.store.session(self.scope, self.session)
        state = self.store.load_snapshot(self.scope, sid, require_current=False)
        candidates = []
        for entry in state["evidence"]:
            ref = Ref(**entry["ref"])
            if ref.name.startswith(self.prefix + "/exports/") and ref.name.endswith("/manifest"):
                candidates.append(ref)
        if len(candidates) != 1:
            raise InvalidRequest("Snapshot must identify exactly one Bridge export.")
        ref = candidates[0]
        manifest = self._manifest(ref)
        current_ref = self._head(self.prefix + "/current")
        if current_ref is None or self.store.get(self.scope, current_ref).content != self.store.get(self.scope, ref).content:
            raise StaleSnapshot("Bridge export has been superseded; choose the exact current publication.")
        saved = self._bundle(manifest)
        try:
            live = CapturedBundle.capture(saved.root)
        except (OSError, InvalidRequest, AccessDenied):
            raise StaleSnapshot("Bridge source is missing or changed; re-import its explicit current bundle.") from None
        if live.raw != saved.raw or live.sources != saved.sources:
            raise StaleSnapshot("Bridge source changed; re-import before compiling current requirements.")
        return manifest, saved, ref

    def packet(self, sid, *, budget=8192, layered=False, intent_details=True):
        # One consistent SQLite view prevents an intervening publication from
        # changing the origin pointer between freshness validation and compile.
        with self.store.read_transaction():
            manifest, _, ref = self._resolve(sid)
            from .loss import packet_loss,report
            working_budget=budget
            while True:
                try:
                    if layered:
                        from .layered import compile_brief
                        packet=compile_brief(Compiler(self.store),self.scope,sid,self.session,budget=working_budget)
                    else:
                        packet=Compiler(self.store)._compile(self.scope,sid,self.session,budget=working_budget,
                            requested=(),layout=None,index=IndexPolicy(max_entries=5 if intent_details else 1))
                except BudgetExceeded:
                    raise BudgetExceeded("Required Bridge IDs " + ", ".join(manifest["required_ids"]) +
                                         " and task/navigation do not fit; increase the context byte budget.") from None
                context=_json(packet.body);loss=packet_loss(context)
                if 'bridge_requirements' not in loss:
                    selected={Ref(**row['ref']) for row in context.get('stable_evidence',[])+context.get('evidence',[])}
                    active=[row for row in manifest['requirements'].values() if row['status']=='active']
                    loss['bridge_requirements']=report('bridge_requirements','requirements',total=len(active),selected=sum(Ref(**row['ref']) in selected for row in active),unknown=0,
                        scope='active_ledger_requirements',recover={'reader':'context.get','ref':manifest['ledger_ref']})
                description=self._describe(manifest,ref,sid)
                if not intent_details and description['resume_intent']['status']=='anchored':
                    value=description['resume_intent']
                    description['resume_intent']={key:value[key] for key in ('status','effective_request','next_action','coverage','ref')}
                    loss={name:{k:v for k,v in dimension.items() if k!='dimensions'} for name,dimension in loss.items()}
                result={**description,'loss':loss,'units':packet.units,'unit':packet.unit,'budget':budget,'context':context}
                size=len(canonical(result).encode('utf-8'))
                if 'resume_intent' not in manifest or size<=budget:return result
                working_budget-=max(1,size-budget)
                if working_budget<=0:raise BudgetExceeded('Required intent envelope exceeds the context budget')

    def receipt(self, sid, path):
        with self.store.read_transaction():
            manifest, bundle, _ = self._resolve(sid)
            proof = _read(path, MAX_LEDGER)
            return self.validator.call("receipt", bundle, scope=manifest["bridge_scope"], receipt=proof)

    def refresh(self, base_snapshot, owner: Lease):
        """Trusted host derives a current view from this exact, still-valid source.

        This is intentionally absent from the model-facing MCP tools.
        """
        with self.store.transaction():
            task = self.store._lease(self.scope, owner)
            _, _, source_ref = self._resolve(base_snapshot)
            base = self.store.load_snapshot(self.scope, base_snapshot, require_current=False)
            if task["objective"] != base["objective"]:
                raise StaleSnapshot("Runtime objective changed; reconcile the explicit Bridge source before refreshing.")
            if task["version"] == base["version"]:
                return base_snapshot
            row = self.store.db.execute(
                "SELECT s.id FROM snapshots s JOIN events e ON e.scope=s.scope AND json_extract(e.metadata,'$.snapshot')=s.id "
                "WHERE s.scope=? AND s.version=? AND e.kind='context.refreshed' "
                "AND json_extract(e.metadata,'$.base_snapshot')=? ORDER BY e.sequence DESC LIMIT 1",
                (self.scope.key, task["version"], base_snapshot)).fetchone()
            if row:
                self.store.load_snapshot(self.scope, row["id"])
                return row["id"]
            choices = tuple(Selection(Ref(**entry["ref"]), required=entry["required"], priority=entry["priority"],
                                      current=entry["current"], cache_stable=entry.get("cache_stable", False))
                            for entry in base["evidence"])
            sid = self.store._snapshot(self.scope, owner, choices)
            self.store._event(self.scope, "context.refreshed", snapshot=sid, base_snapshot=base_snapshot,
                              source_ref=asdict(source_ref))
            return sid
