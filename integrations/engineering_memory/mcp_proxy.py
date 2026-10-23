"""Extend the existing Basic Memory stdio server, preserving unsupported native requests."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from memory_integration import dispatch,extra_tools,mcp_result
from memory_runtime import runtime_from_config
from model_synthesis import generator_from_config

class Adapter:
    def __init__(self,memory,config,home):
        self.memory,self.config,self.home=memory,config,home
        self.list_ids=set()
        self.context_ids={}

    def request(self,message):
        if message.get('method')=='engineering/runtime':
            params=message.get('params',{});name=params.get('name')
            if name not in {'recall','notes','observations','retain','reflect','page','judge','query_revision'}:
                raise ValueError('Unsupported shared memory operation')
            args=params.get('args',[]);kwargs=params.get('kwargs',{})
            if name=='judge':
                from listener import judge
                value=judge(self.config,self.home,*args,**kwargs)
                return {'jsonrpc':'2.0','id':message['id'],'result':mcp_result(value)}
            if kwargs.pop('_synthesize',False) and name in {'reflect','page'}:
                kwargs['generator']=generator_from_config(self.config,self.home,self.memory)
            value=getattr(self.memory,name)(*args,**kwargs)
            return {'jsonrpc':'2.0','id':message['id'],'result':mcp_result(value)}
        if message.get('method')=='tools/list':
            self.list_ids.add(message.get('id'))
            return None
        if message.get('method')!='tools/call': return None
        params=message.get('params',{})
        name=params.get('name');args=params.get('arguments',{})
        main=args.get('project') in (None,'main') and not args.get('project_id') and not args.get('search_all_projects')
        if main and name in {'read_note','read_content'}:
            from checkpoint_lifecycle import from_config
            retired=from_config(self.config,self.home).read(args.get('identifier') or args.get('path') or '')
            if retired and retired['state']!='restored':
                return {'jsonrpc':'2.0','id':message['id'],'result':mcp_result(retired)}
        if main and name in {'recent_activity','build_context','search_notes'}:
            explicit_history=name=='search_notes' and ('checkpoint_history' in args.get('note_types',[]) or
                (args.get('metadata_filters') or {}).get('historical') is True)
            self.context_ids[message.get('id')]=(name,explicit_history)
            if name in {'recent_activity','build_context'}:args['output_format']='json'
        result=dispatch(self.memory,params.get('name'),params.get('arguments',{}),generator_from_config(self.config,self.home,self.memory))
        if result is not None:self.context_ids.pop(message.get('id'),None)
        return None if result is None else {'jsonrpc':'2.0','id':message['id'],'result':result}

    def response(self,message):
        context=self.context_ids.pop(message.get('id'),None)
        if context and 'result' in message and not message['result'].get('isError'):
            from checkpoint_lifecycle import from_config,is_history
            data=from_config(self.config,self.home).load()
            denied=set()
            for record in data['records'].values():
                if record['state']!='restored':denied.update(record[k] for k in ('permalink','title','path'))
            if not context[1]:
                for note in self.memory.notes():
                    if is_history(note['meta']):denied.update(note[k] for k in ('permalink','title','path'))
            def forbidden(value):
                return isinstance(value,str) and any(x in value for x in denied)
            def filtered(value):
                if isinstance(value,list):return [v for item in value if (v:=filtered(item)) is not None]
                if isinstance(value,dict):
                    if any(forbidden(v) for v in value.values() if isinstance(v,str)):return None
                    cleaned={k:filtered(v) for k,v in value.items()}
                    if 'primary_result' in cleaned and cleaned['primary_result'] is None:return None
                    if isinstance(cleaned.get('results'),list):
                        cleaned['total']=len(cleaned['results']);cleaned['total_is_exact']=False
                        cleaned['history_filtered']=True
                    return cleaned
                return None if forbidden(value) else value
            result=message['result'];payload=result.get('structuredContent')
            if payload is not None:payload=payload.get('result',payload)
            else:
                try:payload=json.loads(next(c['text'] for c in result.get('content',[]) if c.get('type')=='text'))
                except (ValueError,StopIteration):payload={'error':'Unstructured automatic context withheld; use exact read_note.'}
            message['result']=mcp_result(filtered(payload) or {'results':[],'history_filtered':True})
        if message.get('id') in self.list_ids:
            self.list_ids.discard(message['id'])
            if 'result' in message:
                names={t['name'] for t in message['result'].get('tools',[])}
                message['result'].setdefault('tools',[]).extend(t for t in extra_tools() if t['name'] not in names)
        return message

def main():
    home=Path(os.environ.get('ENGINEERING_MEMORY_HOME') or Path.home()/'.codex/services/engineering-memory')
    config=json.loads((home/'config.json').read_text(encoding='utf-8'))
    memory=runtime_from_config(config,home);adapter=Adapter(memory,config,home)
    from resource_limits import numeric_environment
    env=os.environ.copy();env.update(numeric_environment());env.update(BASIC_MEMORY_AUTO_UPDATE='false')
    if env.get('MEMORY_PROXY_TRACE')=='true':
        import faulthandler
        faulthandler.enable()
        faulthandler.dump_traceback_later(45,repeat=False)
    if memory.models:
        memory.models.load_dependencies()
    env.update(BASIC_MEMORY_SEMANTIC_EMBEDDING_MODEL=config.get('memory_embedding_model','sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2'),
               BASIC_MEMORY_SEMANTIC_EMBEDDING_PROVIDER='fastembed',BASIC_MEMORY_SEMANTIC_EMBEDDING_DIMENSIONS='384',
               BASIC_MEMORY_RERANKER_ENABLED='true',BASIC_MEMORY_RERANKER_MODEL=config.get('memory_reranker_model','BAAI/bge-reranker-base'),
               BASIC_MEMORY_SEMANTIC_EMBEDDING_CACHE_DIR=str(config['model_cache']),HF_HOME=str(Path(config['model_cache'])/'hf'))
    server=config.get('basic_memory_server')
    # Use the installed console entry point inside the selected Python environment.
    # uv's per-user trampoline can fail Windows directory canonicalization even
    # though this environment can read its installed package and allowed vault.
    command=[server,'mcp'] if server else [sys.executable,'-c','from basic_memory.cli.main import app; app()','mcp']
    inference=None
    if memory.models and config.get('memory_reranker_path'):
        if server:raise ValueError('Compact shared inference requires the managed native Basic Memory entry')
        from shared_inference import InferenceBridge
        inference=InferenceBridge(memory.models)
        env.update(inference.environment())
        command=[sys.executable,str(Path(__file__).with_name('native_memory.py')),'mcp']
    process=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=env,bufsize=0,
                             creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    stdout_lock=threading.Lock();stdin_lock=threading.Lock()
    def emit(message):
        with stdout_lock:
            sys.stdout.buffer.write((json.dumps(message,ensure_ascii=False)+'\n').encode('utf-8'));sys.stdout.buffer.flush()
    def native(message):
        with stdin_lock:
            process.stdin.write((json.dumps(message,ensure_ascii=False)+'\n').encode('utf-8'));process.stdin.flush()
    def handle(message):
        try:
            if env.get('MEMORY_PROXY_TRACE')=='true':
                print('MCP request started: '+str(message.get('id'))+' '+str(message.get('params',{}).get('name') or message.get('method')),file=sys.stderr,flush=True)
            response=adapter.request(message)
            if response is None: native(message)
            else: emit(response)
            if env.get('MEMORY_PROXY_TRACE')=='true':
                print('MCP request dispatched: '+str(message.get('id')),file=sys.stderr,flush=True)
        except Exception as error:
            # Model/retrieval failures are explicit, never silently downgraded to another retrieval contract.
            from listener import clean
            if 'id' in message: emit({'jsonrpc':'2.0','id':message['id'],'result':{'isError':True,'content':[{'type':'text','text':clean(str(error),1000)}]}})
    def output():
        for line in process.stdout:
            try: emit(adapter.response(json.loads(line)))
            except ValueError:
                sys.stderr.buffer.write(b'Non-protocol child stdout ignored.\n');sys.stderr.buffer.flush()
            except BrokenPipeError:break
        if os.environ.get('ENGINEERING_MEMORY_WORKER')=='1':
            # A dead native service must fail the in-flight shared request immediately.
            os._exit(1)
    def stderr():
        for line in process.stderr:
            sys.stderr.buffer.write(line);sys.stderr.buffer.flush()
    threading.Thread(target=output,daemon=True).start();threading.Thread(target=stderr,daemon=True).start()
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            for line in sys.stdin.buffer:
                try: message=json.loads(line)
                except ValueError: continue
                params=message.get('params',{})
                if message.get('method')=='tools/call' and params.get('name') in {'search_notes','memory_recall','memory_reflect','memory_knowledge_page'}:
                    pool.submit(handle,message)
                else: handle(message)
    finally:
        process.stdin.close()
        try: process.wait(timeout=10)
        except subprocess.TimeoutExpired: process.terminate();process.wait(timeout=5)
        memory.close()
        if inference:inference.close()

if __name__=='__main__':main()
