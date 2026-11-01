"""CM10 current host registration -> source-linked CLI/MCP product navigation."""
from dataclasses import asdict
import json
from pathlib import Path
import unittest
import asyncio
import sys

from . import test_claude_mem_compact as setup
from contextrail import Store, Lease
from contextrail.errors import RailError
from contextrail.host import ContextTools
from contextrail.query_sources import QueryBinding


class Deliverables(unittest.TestCase):
    def setUp(self):
        self.f=setup.CompactAssistance('runTest');self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.output=self.f.workspace/'results/交付 report.csv';self.output.parent.mkdir()
        self.output.write_text('name,value\n结果,42\n',encoding='utf-8')
        with Store(self.f.database) as store:
            self.source=store.put(self.f.scope,Lease('owner',0),'delivery-source',b'Host produced the report and declared its URL.',expected_revision=0)
        self.request=self.f.root/'register.json'

    def register(self,identity='report-entry',fields=None,expected=0):
        value={'registration_id':identity,'source':asdict(self.source),'fields':fields or {'deliverables':[{'path':str(self.output),'title':'结果报告'}]},
            'expected_revision':expected}
        self.request.write_text(json.dumps(value,ensure_ascii=False),encoding='utf-8')
        return self.f.run_cli('deliverable-register','--session','owner','--epoch',0,'--input',self.request)

    def tools(self,store):
        return ContextTools(store,self.f.scope,'owner',query_binding=QueryBinding.load(self.f.query,'compact-task',self.f.workspace))

    def test_registered_result_is_found_openable_source_bound_and_idempotent(self):
        first=self.register()
        self.assertEqual(first.returncode,0,first.stderr.decode('utf-8','replace'))
        saved=json.loads(first.stdout)
        again=self.register();self.assertEqual(again.returncode,0)
        self.assertTrue(json.loads(again.stdout)['duplicate'])
        with Store(self.f.database) as store:
            result=self.tools(store).call('context.deliverables',{})
            self.assertEqual(len(result['items']),1)
            product=result['items'][0]
            self.assertEqual(product['open_path'],str(self.output.resolve()))
            self.assertEqual(product['kind'],'spreadsheet')
            self.assertEqual(product['availability']['state'],'metadata_matches')
            self.assertEqual(product['source'],asdict(self.source))
            self.assertEqual(product['task'],'compact-task')
            details=self.tools(store).call('context.deliverable',product['ref'])
            self.assertEqual(details['record_ref'],saved['record_ref'])
            self.assertIn('结果报告',product['markdown_link'])
            self.assertEqual(sum(r['kind']=='deliverable.registered' for r in store.events(self.f.scope)),1)
        listed=self.f.cli('deliverables','--session','owner')
        self.assertEqual(listed['items'][0]['open_path'],str(self.output.resolve()))
        exact_input=self.f.root/'exact-product.json'
        exact_input.write_text(json.dumps(listed['items'][0]['ref']),encoding='utf-8')
        direct=self.f.cli('deliverable','--session','owner','--input',exact_input)
        self.assertEqual(direct['record_ref'],saved['record_ref'])
        self.assertEqual(self.output.read_text(encoding='utf-8'),'name,value\n结果,42\n')

    def test_changed_missing_and_link_only_are_distinct_from_published_success(self):
        result=self.register();self.assertEqual(result.returncode,0,result.stderr.decode('utf-8','replace'))
        self.output.write_text('new,version\n修改,99\n',encoding='utf-8')
        with Store(self.f.database) as store:
            changed=self.tools(store).call('context.deliverables',{})['items'][0]
            self.assertEqual(changed['availability']['state'],'changed')
            self.assertEqual(changed['record_ref'],json.loads(result.stdout)['record_ref'])
        self.output.unlink()
        with Store(self.f.database) as store:
            missing=self.tools(store).call('context.deliverables',{})['items'][0]
            self.assertEqual(missing['availability']['state'],'missing')
        linked=self.register('link-entry',{'pr_url':'https://github.com/example/repo/pull/42'})
        self.assertEqual(linked.returncode,0,linked.stderr.decode('utf-8','replace'))
        with Store(self.f.database) as store:
            rows=self.tools(store).call('context.deliverables',{})['items']
            url=next(row for row in rows if row.get('url'))
            self.assertEqual(url['kind'],'pull-request');self.assertEqual(url['publication_state'],'declared_link_not_verified_publication')
            self.assertEqual(url['availability']['state'],'not_checked_remote')

    def test_invalid_location_or_secret_url_is_rejected_and_other_task_cannot_read_record(self):
        rejected=self.register(fields={'deliverable':{'url':'https://user:secret@example.test/report'}})
        self.assertNotEqual(rejected.returncode,0)
        self.assertEqual(json.loads(rejected.stderr)['error']['code'],'invalid_request')
        outside=self.f.root/'outside.csv';outside.write_text('no access',encoding='utf-8')
        rejected=self.register(fields={'deliverable':{'path':str(outside)}})
        self.assertNotEqual(rejected.returncode,0)
        self.assertEqual(json.loads(rejected.stderr)['error']['code'],'access_denied')
        registered=self.register();self.assertEqual(registered.returncode,0)
        other=self.f.cli('project-init','--session','another-owner','--task','other-task','--objective','another task')
        from contextrail import Scope
        with Store(self.f.database) as store:
            reader=ContextTools(store,Scope(**other['scope']),'another-owner')
            self.assertEqual(reader.call('context.deliverables',{})['items'],[])
            with self.assertRaises(RailError):reader.call('context.deliverable',{'record_ref':json.loads(registered.stdout)['record_ref'],'index':0})

    def test_explicit_version_update_preserves_historical_record_and_existing_state_projects(self):
        first=self.register();self.assertEqual(first.returncode,0)
        original=json.loads(first.stdout)['record_ref']
        with Store(self.f.database) as store:
            self.source=store.put(self.f.scope,Lease('owner',0),self.source.name,b'New version is explicitly registered.',expected_revision=1)
        conflict=self.register();self.assertNotEqual(conflict.returncode,0)
        self.assertEqual(json.loads(conflict.stderr)['error']['code'],'conflict')
        updated=self.register(expected=1);self.assertEqual(updated.returncode,0,updated.stderr.decode('utf-8','replace'))
        with Store(self.f.database) as store:
            current=self.tools(store).call('context.deliverables',{})
            self.assertEqual(len(current['items']),1)
            self.assertEqual(current['items'][0]['record_ref']['revision'],2)
            old=self.tools(store).call('context.deliverable',{'record_ref':original,'index':0})
            self.assertEqual(old.get('record_state'),'historical_revision')
            self.assertEqual(old['source']['revision'],1)
            state=store.add_task_state(self.f.scope,Lease('owner',0),kind='decision',text=json.dumps({'docs_url':'https://example.test/docs'}),sources=(self.source,))
            projected=self.tools(store).call('context.deliverables',{})
            item=next(row for row in projected['items'] if row.get('url')=='https://example.test/docs')
            self.assertEqual(item['ref']['task_state_id'],state.id)
            exact=self.tools(store).call('context.deliverable',item['ref'])
            self.assertEqual(exact['url'],'https://example.test/docs')

    def test_registered_product_is_readable_through_real_mcp_stdio(self):
        registered=self.register();self.assertEqual(registered.returncode,0)
        from mcp import ClientSession,StdioServerParameters
        from mcp.client.stdio import stdio_client
        async def read():
            params=StdioServerParameters(command=sys.executable,args=['-m','contextrail','mcp','--workspace',str(self.f.workspace),
                '--session','owner','--query-binding',str(self.f.query)],cwd=str(Path.cwd()))
            async with stdio_client(params) as (source,sink):
                async with ClientSession(source,sink) as client:
                    await client.initialize()
                    catalog=await client.list_tools()
                    self.assertTrue({'context.deliverables','context.deliverable','context.file_history'} <= {tool.name for tool in catalog.tools})
                    listing=await client.call_tool('context.deliverables',{})
                    self.assertFalse(getattr(listing,'is_error',False) or getattr(listing,'isError',False))
                    values=json.loads(listing.content[0].text)
                    self.assertEqual(values['items'][0]['open_path'],str(self.output.resolve()))
                    detail=await client.call_tool('context.deliverable',values['items'][0]['ref'])
                    self.assertFalse(getattr(detail,'is_error',False) or getattr(detail,'isError',False))
                    self.assertEqual(json.loads(detail.content[0].text)['source'],asdict(self.source))
                    rejected=await client.call_tool('context.deliverable',{**values['items'][0]['ref'],'workspace':'another-scope'})
                    self.assertTrue(getattr(rejected,'is_error',False) or getattr(rejected,'isError',False))
                    self.assertEqual(json.loads(rejected.content[0].text)['error']['code'],'invalid_request')
        asyncio.run(read())


if __name__=='__main__':unittest.main()
