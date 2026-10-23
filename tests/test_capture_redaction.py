"""Current F04 consumer contract: redact before the existing queue persists."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


APP = Path.home() / '.codex/services/engineering-memory/app'
OVERLAY = Path(__file__).resolve().parents[1] / 'integrations/engineering_memory'


class CaptureRedactionTests(unittest.TestCase):
    def memory_call(self, script, request):
        with tempfile.TemporaryDirectory(prefix='rail-redaction-') as temp:
            child = subprocess.run([sys.executable, '-c',
                'import json,sys\nfrom pathlib import Path\n'
                'sys.stdout.reconfigure(encoding="utf-8")\n'
                'sys.path[:0]=sys.argv[1:3]\n'
                'home=Path(sys.argv[3])\nrequest=json.loads(sys.stdin.read())\n' + script,
                str(OVERLAY), str(APP), temp], input=json.dumps(request), text=True,
                encoding='utf-8', capture_output=True, timeout=35)
            self.assertEqual(child.returncode, 0, child.stderr)
            return json.loads(child.stdout)

    def test_document_redacted_before_native_queue_persistence(self):
        secrets = ['FAKE_CANARY_KEY_74291', 'FAKE_CANARY_PASS_74291', 'FAKE_CANARY_AUTH_74291']
        content = json.dumps({'nested': {'api_key': secrets[0], 'password': secrets[1],
            'headers': {'Authorization': 'Bearer ' + secrets[2]}}, 'message': 'keep source role'})
        jobs = self.memory_call('from listener import Store\nfrom history_import import enqueue\n'
            'store=Store(home)\n'
            'enqueue(store,"selected.json",home,"document:revision1",request,"2026-10-23T00:00:00Z","document")\n'
            'print(json.dumps([json.loads(r[0]) for r in store.db.execute("SELECT payload FROM jobs")]))\n'
            'store.db.close()\n', content)
        self.assertEqual(len(jobs), 1)
        serialized = json.dumps(jobs)
        for secret in secrets:
            self.assertFalse(secret in serialized, 'credential reached durable jobs payload')
        self.assertIn('keep source role', jobs[0]['final'])

    def test_native_listener_persists_processed_candidate_and_cursor(self):
        secret = 'FAKE_NATIVE_PASSWORD_63201'
        rows = [
            {'type': 'session_meta', 'payload': {'id': 'native-session', 'cwd': 'D:/project'}},
            {'type': 'response_item', 'payload': {'type': 'message', 'role': 'user', 'content':
                [{'type': 'input_text', 'text': '记住：password=' + secret + '，保留用户角色'}]}},
            {'type': 'event_msg', 'timestamp': '2026-10-23T00:00:00Z', 'payload':
                {'type': 'task_complete', 'turn_id': 'turn1', 'last_agent_message': '以后按这个约定执行，因为已确认。' * 10}}]
        result = self.memory_call('from listener import Store\n'
            'path=home/"native.jsonl"\npath.write_text("".join(json.dumps(r)+"\\n" for r in request),encoding="utf-8")\n'
            'store=Store(home)\nstore.ingest(path,"codex")\n'
            'print(json.dumps({"jobs":[json.loads(r[0]) for r in store.db.execute("SELECT payload FROM jobs")],'
            '"cursors":[json.loads(r[0]) for r in store.db.execute("SELECT context FROM cursors")]}))\n'
            'store.db.close()\n', rows)
        self.assertEqual(len(result['jobs']), 1)
        self.assertFalse(secret in json.dumps(result), 'credential reached native durable state')
        self.assertEqual(result['jobs'][0]['_capture']['state'], 'processed')
        self.assertIn('user', result['jobs'][0])

    def test_retain_sanitizes_claim_and_quote_before_authoritative_write(self):
        secret = 'FAKE_RETAIN_PASSWORD_63591'
        result = self.memory_call('from memory_runtime import MemoryRuntime\n'
            'vault=home/"vault"\nvault.mkdir()\nruntime=MemoryRuntime(vault,home/"state.sqlite",use_models=False)\n'
            'claims=[{"subject":"demo","predicate":"credential example","value":request,"quote":request,"source":"user"}]\n'
            'sources={"user":"password="+request,"final":""}\n'
            'first=runtime.retain("fact:1",claims,sources,"2026-10-23T00:00:00Z")\n'
            'second=runtime.retain("fact:1",claims,sources,"2026-10-23T00:00:00Z")\n'
            'body=Path(first["source"]).read_text(encoding="utf-8")\n'
            'print(json.dumps({"body":body,"duplicate":second["duplicate"]}))\nruntime.close()\n', secret)
        self.assertFalse(secret in result['body'], 'credential reached authoritative memory source')
        self.assertIn('[REDACTED]', result['body'])
        self.assertTrue(result['duplicate'])

    def test_old_ready_job_is_processed_before_save_without_model_call(self):
        secret = 'FAKE_OLD_JOB_PASS_48392'
        result = self.memory_call('from listener import Store,Service\nimport time\n'
            'vault=home/"vault"\nvault.mkdir()\nstore=Store(home)\n'
            'payload={"user":"password="+request,"final":"keep final role","kind":"document","source":"doc","offset":0}\n'
            'envelope={"judgment":{"notes":[],"diagnostic":{"password":request}},"existing":[]}\n'
            'with store.db:store.db.execute("INSERT INTO jobs(id,source,payload,state,created,available,result) VALUES(?,?,?,?,?,?,?)",'
            '("old-job","doc",json.dumps(payload),"ready_to_save",time.time(),0,json.dumps(envelope)))\n'
            'service=Service.__new__(Service)\nservice.home=home\nservice.store=store\nservice.config={"vault":str(vault)}\nservice.memory=None\n'
            'service.process_one()\nrow=store.db.execute("SELECT state,payload,result,error FROM jobs").fetchone()\n'
            'print(json.dumps(dict(row)))\nstore.db.close()\n', secret)
        self.assertEqual(result['state'], 'skipped')
        self.assertFalse(secret in json.dumps(result), 'old job reached save with unprocessed persisted values')

    def test_rejected_projection_never_publishes_partial_derivative(self):
        from contextrail import Store, Target, Scope, Lease
        from contextrail.redacted_evidence import redact_evidence
        from contextrail.errors import AccessDenied, Conflict, InvalidRequest
        with Store(':memory:') as store:
            scope=Scope('local','redaction','main','boundaries')
            owner=store.create_task(scope,Target('owner','host','unknown'),'redact',allowed_providers=('host',))
            source=store.put(scope,owner,'binary-source',b'\xff',expected_revision=0)
            for lease, error in ((Lease('other',0),AccessDenied),(owner,InvalidRequest)):
                with self.assertRaises(error):
                    redact_evidence(store,scope,lease,source,'tool')
                self.assertEqual(len(store.search(scope,'redacted')),0)
            with store.db:
                store.db.execute('UPDATE tasks SET handoff=? WHERE scope=?',('fixture-frozen',scope.key))
            with self.assertRaises(Conflict):
                redact_evidence(store,scope,owner,source,'tool')
            self.assertEqual(store.db.execute('SELECT count(*) FROM artifacts').fetchone()[0],1)

    def test_processed_marker_does_not_bypass_nested_and_argv_redaction(self):
        from contextrail.sanitization import sanitize_capture, CaptureError
        raw = {'sanitized': True, 'role': 'user', 'payload': {'password': 'tiny'},
               'argv': ['tool', '--password', 'FAKE_ARGV_PASS_43218', '--other', 'keep'],
               'echo': 'FAKE_ARGV_PASS_43218'}
        processed = sanitize_capture(raw, 'tool')
        self.assertFalse('FAKE_ARGV_PASS_43218' in processed.payload)
        self.assertEqual(processed.value['payload']['password'], '[REDACTED]')
        self.assertEqual(processed.value['argv'][-1], 'keep')
        self.assertIs(sanitize_capture(processed, 'tool'), processed)
        self.assertEqual(sanitize_capture(processed.value, 'tool').value, processed.value)
        with self.assertRaises(CaptureError):
            sanitize_capture(processed, 'user')

    def test_upstream_shapes_hidden_token_private_key_and_utf8_prose(self):
        from contextrail.sanitization import sanitize_capture
        token = 'sk-FAKE0123456789abcdef0123'
        key_body = 'b3BlbnNzaC1rZXktdjEAAAAA'
        input_text = '中文🙂 keep\n' + token[:3] + '\u200b' + token[3:] + '\n' + \
            'postgres://demo:FAKE_DB_PASS_31901@localhost/db\n' + \
            '-----BEGIN PRIVATE KEY-----\n' + key_body + '\n-----END PRIVATE KEY-----\nafter'
        result = sanitize_capture(input_text, 'tool').value
        for secret in (token[3:], key_body, 'FAKE_DB_PASS_31901'):
            self.assertFalse(secret in result, 'upstream credential shape survived capture')
        self.assertIn('中文🙂 keep', result)
        self.assertTrue(result.endswith('after'))
        self.assertEqual(sanitize_capture(result, 'tool').value, result)

    def test_invalid_capture_errors_do_not_echo_values(self):
        from contextrail.sanitization import sanitize_capture, CaptureError
        cycle = {'password': 'FAKE_ERROR_PASS_32101'}
        cycle['next'] = cycle
        for value in (cycle, {'value': object()}, {'value': float('nan')}, 'x' * (4 * 1024 * 1024 + 1)):
            with self.assertRaises(CaptureError) as raised:
                sanitize_capture(value, 'tool')
            self.assertFalse('FAKE_ERROR_PASS_32101' in str(raised.exception))

    def test_cli_redacted_projection_keeps_raw_and_uses_own_byte_offsets(self):
        from dataclasses import asdict
        from contextrail import Store, Target, Ref
        from contextrail.project import load_project
        with tempfile.TemporaryDirectory(prefix='rail-projection-') as temp:
            root = Path(temp)
            (root / '.contextrail.toml').write_text('[project]\nid="projection"\ntask="redact"\n', encoding='utf-8')
            config = load_project(root)
            config.store.parent.mkdir(parents=True)
            raw = '中文🙂 password=FAKE_PROJECTION_PASS_71240\n后续内容'.encode()
            with Store(config.store) as store:
                owner = store.create_task(config.scope, Target('owner','host','external'), 'redact source', allowed_providers=('host',))
                source = store.put(config.scope, owner, 'raw-source', raw, expected_revision=0)
            child = subprocess.run([sys.executable, '-m', 'contextrail', 'redact-evidence', '--workspace', str(root),
                '--session','owner','--epoch','0','--source-ref',json.dumps(asdict(source)), '--role','tool'],
                capture_output=True, text=True, encoding='utf-8', timeout=25)
            self.assertEqual(child.returncode, 0, child.stderr)
            result = json.loads(child.stdout)
            with Store(config.store) as store:
                self.assertEqual(store.get(config.scope,source).content, raw)
                body = store.get(config.scope,Ref(**result['processed_ref'])).content
                self.assertFalse(b'FAKE_PROJECTION_PASS_71240' in body)
                manifest = json.loads(store.get(config.scope,Ref(**result['manifest_ref'])).content)
                self.assertEqual(manifest['source_ref'], asdict(source))
                start = body.index('后续'.encode())
                self.assertNotEqual(start, raw.index('后续'.encode()))
                ref = Ref(result['processed_ref']['name'], result['processed_ref']['revision'], start, start+len('后续'.encode()))
                self.assertEqual(store.get(config.scope,ref).content.decode(), '后续')


if __name__ == '__main__':
    unittest.main()
