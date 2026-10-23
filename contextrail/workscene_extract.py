"""Processed event candidates shared by the original M capture and R import."""
from ._vendor.context_mode_scene import call_facts,message_facts,result_info
import json


def bounded(value,maximum=4096):
    def encode(v):return json.dumps(v,ensure_ascii=False,separators=(',',':')).encode('utf-8')
    raw=encode(value)
    if len(raw)<=maximum:return value
    def preview(v,width,depth=0):
        if isinstance(v,str):return v.encode('utf-8')[:width].decode('utf-8','ignore')
        if isinstance(v,list):return [preview(x,width,depth+1) for x in v[:8]] if depth<4 else []
        if isinstance(v,dict):return {k:preview(x,width,depth+1) for k,x in list(v.items())[:16]} if depth<4 else {}
        return v
    for width in (512,256,128,64):
        result=preview(value,width)
        if len(encode(result))<=maximum-256:break
    result['projection_loss']={'total_utf8_bytes':len(raw),'projected_utf8_bytes':len(encode(result)),
                               'truncated':True,'recover':'exact source refs; projection is not a full source copy'}
    return result


def projection(event_type,data,role):
    candidates=call_facts(data) if event_type=='tool.call' else message_facts(data,role) if role in {'user','assistant'} else []
    return bounded({'schema':'contextrail.scene-candidates.v1','candidates':candidates,'authority':'source_declarations_not_completed_effects'},8192)
