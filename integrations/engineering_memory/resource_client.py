"""Small per-chat transport. Never starts an unbounded fallback model server."""
import json
import os
from pathlib import Path
import socket
import struct
import sys

MAX_REQUEST=1024*1024
MAX_RESPONSE=8*1024*1024

def receive(sock,limit):
    def exact(n):
        data=bytearray()
        while len(data)<n:
            part=sock.recv(n-len(data))
            if not part:raise ConnectionError('Memory service disconnected; request outcome may be unknown')
            data.extend(part)
        return data
    length=struct.unpack('!I',exact(4))[0]
    if not 0<length<=limit:raise ValueError('Memory message exceeds configured size budget')
    return json.loads(exact(length))

def send(sock,value,limit):
    data=json.dumps(value,ensure_ascii=False).encode('utf-8')
    if len(data)>limit:raise ValueError('Memory message exceeds configured size budget')
    sock.sendall(struct.pack('!I',len(data))+data)

def rpc(home,message,timeout=145):
    try:endpoint=json.loads((Path(home)/'resource-endpoint.json').read_text(encoding='utf-8'))
    except (OSError,ValueError) as exc:
        raise RuntimeError('Existing EngineeringMemoryListener resource service is unavailable; no private model fallback was started') from exc
    with socket.create_connection(('127.0.0.1',endpoint['port']),timeout=3) as sock:
        sock.settimeout(timeout)
        send(sock,{'token':endpoint['token'],'message':message},MAX_REQUEST)
        result=receive(sock,MAX_RESPONSE)
    if 'broker_error' in result:raise RuntimeError(result['broker_error'])
    return result

class RemoteRuntime:
    def __init__(self,home):
        self.home=Path(home);self._db=None
    @property
    def db(self):
        # Hook injection receipts only; models and search never live in this process.
        if self._db is None:
            import sqlite3
            self._db=sqlite3.connect(self.home/'state.sqlite',timeout=30)
        return self._db
    def invoke(self,name,*args,**kwargs):
        response=rpc(self.home,{'jsonrpc':'2.0','id':1,'method':'engineering/runtime',
                              'params':{'name':name,'args':args,'kwargs':kwargs}})
        if 'error' in response:raise RuntimeError(response['error']['message'])
        result=response['result']
        if result.get('isError'):raise RuntimeError(result['content'][0]['text'])
        return result['structuredContent']['result']
    def recall(self,*args,**kwargs):return self.invoke('recall',*args,**kwargs)
    def notes(self):return self.invoke('notes')
    def observations(self,*args,**kwargs):return self.invoke('observations',*args,**kwargs)
    def retain(self,*args,**kwargs):return self.invoke('retain',*args,**kwargs)
    def reflect(self,query,scope=None,generator=None):return self.invoke('reflect',query,scope,_synthesize=generator is not None)
    def page(self,name,query,scope=None,refresh=False,generator=None,*,history=False,version=None):
        return self.invoke('page',name,query,scope,refresh,_synthesize=generator is not None,history=history,version=version)
    def close(self):
        if self._db:self._db.close();self._db=None

def main():
    home=Path(os.environ.get('ENGINEERING_MEMORY_HOME') or Path.home()/'.codex/services/engineering-memory')
    while True:
        line=sys.stdin.buffer.readline(MAX_REQUEST+1)
        if not line:break
        message={}
        try:
            if len(line)>MAX_REQUEST:
                while line and not line.endswith(b'\n'):line=sys.stdin.buffer.readline(MAX_REQUEST+1)
                raise ValueError('MCP input exceeds 1 MiB request budget')
            message=json.loads(line)
            if not isinstance(message,dict):raise ValueError('MCP request must be an object')
            if 'id' not in message:continue
            response=rpc(home,message)
        except Exception as exc:
            response={'jsonrpc':'2.0','id':message.get('id') if isinstance(message,dict) else None,'error':{'code':-32000,'message':str(exc)[:600]}}
        sys.stdout.buffer.write((json.dumps(response,ensure_ascii=False)+'\n').encode('utf-8'));sys.stdout.buffer.flush()

if __name__=='__main__':main()
