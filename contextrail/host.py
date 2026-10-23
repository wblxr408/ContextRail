"""Small read-only tool mount for a trusted host's authenticated session.

This is a Python SDK boundary, not a claim to implement MCP or any provider API.
"""

import base64
from dataclasses import asdict

from .errors import InvalidRequest
from .models import Ref, Scope, integer
from .store import Store


class ContextTools:
    def __init__(self, store: Store, scope: Scope, session: str, *, max_read_bytes: int = 65536, query_binding=None):
        integer(max_read_bytes, 1)
        self.store, self.scope, self.session_id = store, scope, session
        self.max_read_bytes = max_read_bytes
        self.query_binding=query_binding
        self.store.session(scope, session)

    def call(self, name: str, arguments: dict) -> dict:
        # No scope, SQL, path or provider override is accepted from the model.
        self.store.session(self.scope, self.session_id)
        if not isinstance(arguments, dict):
            raise InvalidRequest("Tool arguments must be an object.")
        if name=='context.metrics':
            from .accounting import metrics
            self._keys(arguments,required=set(),optional={'offset','limit','budget'})
            return metrics(self,**{**arguments,'budget':arguments.get('budget',min(16384,self.max_read_bytes))})
        if name=='context.efficiency':
            from .accounting import efficiency
            return efficiency(**arguments)
        if name in {'context.brief','context.explain'}:
            from .context import Compiler
            import json
            self._keys(arguments,required={'snapshot'},optional={'budget','requested','offset','limit','trace_offset','trace_limit'})
            budget=arguments.get('budget',min(16384,self.max_read_bytes));integer(budget,1)
            if budget>self.max_read_bytes:raise InvalidRequest('Brief budget exceeds the bound reader limit')
            requested=arguments.get('requested',[])
            if not isinstance(requested,list) or len(requested)>100:raise InvalidRequest('Supply at most 100 exact requested refs')
            try:refs=tuple(Ref(**r) for r in requested)
            except (TypeError,ValueError):raise InvalidRequest('Invalid requested evidence reference') from None
            packet=Compiler(self.store).brief(self.scope,arguments['snapshot'],self.session_id,**{**{k:v for k,v in arguments.items() if k not in {'snapshot','requested','budget'}},'budget':budget,'requested':refs})
            return json.loads(packet.body)
        if name=='context.query':
            from .federation import query
            self._keys(arguments,required={'queries'},optional={'view','limits','offsets','budget','source_bytes','refresh','since','until'})
            budget=arguments.get('budget',min(16384,self.max_read_bytes))
            integer(budget,1)
            if budget>self.max_read_bytes:raise InvalidRequest('Query budget exceeds the bound reader limit')
            return query(self,**{**arguments,'budget':budget})
        if name=='context.status':
            from .local_status import status
            self._keys(arguments,required=set(),optional={'snapshot','budget'})
            return status(self,**{**arguments,'budget':arguments.get('budget',min(8192,self.max_read_bytes))})
        if name=='context.timeline':
            from .timeline import timeline
            self._keys(arguments,required=set(),optional={'offset','limit','budget','native_session','file','role','origin','error_category','since','until','around'})
            budget=arguments.get('budget',min(8192,self.max_read_bytes))
            integer(budget,1)
            if budget>self.max_read_bytes:raise InvalidRequest('Timeline budget exceeds the bound reader limit')
            return timeline(self.store,self.scope,self.session_id,**{**arguments,'budget':budget})
        if name == "context.directory_status":
            from .directory_sync import directory_status
            self._keys(arguments, required={"run"}, optional={"offset", "limit", "budget"})
            budget = arguments.get("budget", min(8192, self.max_read_bytes))
            integer(budget, 1)
            if budget > self.max_read_bytes:
                raise InvalidRequest("Directory status budget exceeds the bound reader limit.")
            return directory_status(self.store, self.scope, self.session_id, **{**arguments, "budget": budget})
        if name == "context.result_search":
            from .result_retrieval import search_result
            self._keys(arguments, required={"result_ref", "query"}, optional={"limit", "offset", "budget", "context_chars", "window_limit"})
            budget = arguments.get("budget", min(8192, self.max_read_bytes))
            integer(budget, 1)
            if budget > self.max_read_bytes:
                raise InvalidRequest("Result search budget exceeds the bound reader limit.")
            return search_result(self.store, self.scope, self.session_id, **{**arguments, "budget": budget})
        if name == "context.index_status":
            self._keys(arguments, required=set(), optional=set())
            return self.store.index_status(self.scope)
        if name == "context.search_indexed":
            self._keys(arguments, required={"query"}, optional={"limit", "offset", "budget", "context_chars", "window_limit"})
            budget = arguments.get("budget", min(8192, self.max_read_bytes))
            integer(budget, 1)
            if budget > self.max_read_bytes:
                raise InvalidRequest("Indexed search budget exceeds the bound reader limit.")
            return self.store.search_indexed(self.scope, **{**arguments, "budget": budget})
        if name == "context.batch":
            from .batch import read_batch
            self._keys(arguments, required={"calls"}, optional={"concurrency", "per_call_bytes", "budget"})
            return read_batch(self, **{**arguments, "budget": arguments.get("budget", min(8192, self.max_read_bytes))})
        if name == "context.extract":
            from .extraction import extract_evidence
            self._keys(arguments, required={"source", "kind"}, optional={"options", "limit", "budget"})
            budget = arguments.get("budget", min(8192, self.max_read_bytes))
            integer(budget, 1)
            if budget > self.max_read_bytes:
                raise InvalidRequest("Extraction budget exceeds the bound response limit.")
            return extract_evidence(self.store, self.scope, self.session_id, **{**arguments, "budget": budget})
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
            {'name':'context.metrics','description':'Read persisted request/tool observations, recovery bytes and bound native usage. Unknown final payloads, unobserved calls and savings remain unknown. No model calls.',
             'input_schema':{'type':'object','properties':{'offset':{'type':'integer','minimum':0},'limit':{'type':'integer','minimum':1,'maximum':100},'budget':{'type':'integer','minimum':2048,'maximum':65536}},'additionalProperties':False}},
            {'name':'context.efficiency','description':'Read-only explicit estimate of compaction/cache amortization. Missing costs stay unknown; no generation or model call is started.',
             'input_schema':{'type':'object','properties':{'action':{'type':'string','enum':['compaction','cache']},'trigger':{'type':'string'},'unit':{'type':'string'},
                **{key:{'type':['integer','null'],'minimum':0} for key in ('before_input','after_input','future_turns','generation_cost','validation_cost','expected_recovery_cost','cache_loss_cost','cache_write_cost')}},'required':['action','trigger','unit'],'additionalProperties':False}},
            *[{'name':name,'description':'Generate an exact snapshot layered brief and explain this generated selection, source gaps, Bridge requirements, action states and timeline navigation. No model calls; previews are not full evidence or consumption proof.',
               'input_schema':{'type':'object','properties':{'snapshot':{'type':'string'},'budget':{'type':'integer','minimum':1,'maximum':65536},
                   'offset':{'type':'integer','minimum':0},'limit':{'type':'integer','minimum':1,'maximum':100},
                   'trace_offset':{'type':'integer','minimum':0},'trace_limit':{'type':'integer','minimum':1,'maximum':20},
                   'requested':{'type':'array','maxItems':100,'items':{'type':'object','properties':{'name':{'type':'string'},'revision':{'type':'integer'},'start':{'type':'integer'},'end':{'type':['integer','null']}},'required':['name','revision'],'additionalProperties':False}}},
                   'required':['snapshot'],'additionalProperties':False}} for name in ('context.brief','context.explain')],
            {'name':'context.query','description':'Batch search task evidence, explicitly bound native events and existing main memory. Source failures and history roles remain explicit; refresh and exact refs stay available.',
             'input_schema':{'type':'object','properties':{
                 'queries':{'type':'array','minItems':1,'maxItems':8,'items':{'type':'string','minLength':1,'maxLength':1024}},
                 'view':{'type':'string','enum':['relevance','timeline']},'budget':{'type':'integer','minimum':2048,'maximum':65536},
                 'source_bytes':{'type':'integer','minimum':2048,'maximum':16384},'refresh':{'type':'boolean'},
                 'limits':{'type':'object','properties':{s:{'type':'integer','minimum':1,'maximum':20} for s in ('task','events','memory')},'additionalProperties':False},
                 'offsets':{'type':'object','properties':{s:{'type':'integer','minimum':0,'maximum':10000} for s in ('task','events','memory')},'additionalProperties':False},
                 'since':{'type':'string'},'until':{'type':'string'}},'required':['queries'],'additionalProperties':False}},
            {'name':'context.status','description':'Explain current task, requirements, observed capture, workspace learning queue, source errors and actual adapter capability evidence; no model call or liveness probe.',
             'input_schema':{'type':'object','properties':{'snapshot':{'type':'string','minLength':1},'budget':{'type':'integer','minimum':2048,'maximum':65536}},'additionalProperties':False}},
            {'name':'context.timeline','description':'Browse task-owned event evidence by actual occurrence, with exact refs, unknown times and source gaps. Error categories are derived heuristics.',
             'input_schema':{'type':'object','properties':{
                 'offset':{'type':'integer','minimum':0,'maximum':1000000},'limit':{'type':'integer','minimum':1,'maximum':100},'budget':{'type':'integer','minimum':1024,'maximum':65536},
                 'native_session':{'type':'string','minLength':1},'file':{'type':'string','minLength':1},'around':{'type':'string','minLength':1},
                 'role':{'type':'string','enum':['user','assistant','tool','lifecycle']},'origin':{'type':'string','enum':['native_event','tool_result']},
                 'error_category':{'type':'string','enum':['file_not_found','command_not_found','edit_match_failed','test_failed','syntax_error','runtime_error','permission_denied','git_conflict','timeout','unknown']},
                 'since':{'type':'string'},'until':{'type':'string'}},'additionalProperties':False}},
            {"name": "context.directory_status", "description": "Page observed directory entries and exact refs in this task; shows incomplete coverage and continuation, without filesystem reads or writes.",
             "input_schema": {"type": "object", "properties": {"run": {"type": "string", "minLength": 1},
                 "offset": {"type": "integer", "minimum": 0, "maximum": 1000000},
                 "limit": {"type": "integer", "minimum": 1, "maximum": 100}, "budget": {"type": "integer", "minimum": 1}},
                 "required": ["run"], "additionalProperties": False}},
            {"name": "context.result_search", "description": "Search only the original stdout/stderr of a saved call; change query or use exact refs after no match.",
             "input_schema": {"type": "object", "properties": {
                 "result_ref": {"type": "object", "properties": {"name": {"type": "string"}, "revision": {"type": "integer", "minimum": 1},
                    "start": {"type": "integer", "minimum": 0}, "end": {"type": ["integer", "null"], "minimum": 0}},
                    "required": ["name", "revision"], "additionalProperties": False},
                 "query": {"type": "string", "minLength": 1, "maxLength": 1024},
                 "limit": {"type": "integer", "minimum": 1, "maximum": 20}, "offset": {"type": "integer", "minimum": 0, "maximum": 10000},
                 "context_chars": {"type": "integer", "minimum": 0, "maximum": 256}, "window_limit": {"type": "integer", "minimum": 1, "maximum": 8},
                 "budget": {"type": "integer", "minimum": 1}}, "required": ["result_ref", "query"], "additionalProperties": False}},
            {"name": "context.search_indexed", "description": "Search current task evidence using FTS5/BM25 spans with explicit index coverage. Exact refs remain readable.",
             "input_schema": {"type": "object", "properties": {"query": {"type": "string", "minLength": 1, "maxLength": 1024},
                 "limit": {"type": "integer", "minimum": 1, "maximum": 100}, "offset": {"type": "integer", "minimum": 0, "maximum": 10000},
                 "context_chars": {"type": "integer", "minimum": 0, "maximum": 256}, "window_limit": {"type": "integer", "minimum": 1, "maximum": 8},
                 "budget": {"type": "integer", "minimum": 1}}, "required": ["query"], "additionalProperties": False}},
            {"name": "context.index_status", "description": "Read current task derived index coverage. Does not rebuild or write.",
             "input_schema": {"type": "object", "properties": {}, "additionalProperties": False}},
            {"name": "context.batch", "description": "Run independent bound get/extract reads, preserving ordered per-call failures and retry requests.",
             "input_schema": {"type": "object", "properties": {
                 "calls": {"type": "array", "maxItems": 32, "items": {"type": "object", "properties": {
                     "id": {"type": "string", "minLength": 1}, "tool": {"type": "string", "enum": ["context.get", "context.extract"]},
                     "arguments": {"type": "object"}}, "required": ["id", "tool", "arguments"], "additionalProperties": False}},
                 "concurrency": {"type": "integer", "minimum": 1, "maximum": 8},
                 "per_call_bytes": {"type": "integer", "minimum": 1}, "budget": {"type": "integer", "minimum": 1}},
                 "required": ["calls"], "additionalProperties": False}},
            {"name": "context.extract", "description": "Extract fields/statistics from saved evidence with exact source spans. No execution.",
             "input_schema": {"type": "object", "properties": {
                 "source": {"type": "object", "properties": {"name": {"type": "string"}, "revision": {"type": "integer", "minimum": 1},
                            "start": {"type": "integer", "minimum": 0}, "end": {"type": ["integer", "null"], "minimum": 0}},
                            "required": ["name", "revision"], "additionalProperties": False},
                 "kind": {"type": "string", "enum": ["log", "json", "csv", "git_status", "git_numstat"]},
                 "options": {"type": "object"}, "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                 "budget": {"type": "integer", "minimum": 1}},
                 "required": ["source", "kind"], "additionalProperties": False}},
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
