"""Bounded HTTP response reader for model and embedding providers."""
import json


def read_json(response, max_bytes, budget=None):
    pieces=[]
    size=0
    while True:
        if budget is not None: budget.check_time()
        # HTTPResponse.read1 returns after a bounded network read, allowing a deadline check.
        read=getattr(response,'read1',response.read)
        part=read(min(65536,max_bytes+1-size))
        if not part: break
        size+=len(part)
        if size>max_bytes: raise ValueError('模型服务响应超过大小限制')
        pieces.append(part)
    return json.loads(b''.join(pieces))
