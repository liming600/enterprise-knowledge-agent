"""BM25 + dense vector recall with RRF, operating only on pre-authorized documents."""
import hashlib,json,math,re,threading,urllib.request
from collections import Counter
from llm import configuration
from guardrails import BudgetExceeded
from http_utils import read_json

ALIASES={'多长时间':'期限','多少天':'期限','几天':'期限','多久':'期限','报账':'报销','休假':'年假','带薪假':'年假'}
STOP={'制度','规定','员工','多少','什么','可以','如何','请问','相关','公司','期限','是否','需要','请问'}

def normalize(s):
 for a,b in ALIASES.items(): s=s.replace(a,b)
 return s.lower()

def tokens(text):
 text=normalize(text); parts=re.findall(r'[\u4e00-\u9fff]+|[a-z0-9]+',text); out=[]
 for p in parts:
  if re.fullmatch(r'[\u4e00-\u9fff]+',p): out.extend(p[i:i+2] for i in range(len(p)-1) if p[i:i+2] not in STOP)
  elif len(p)>1: out.append(p)
 return out

def chunks(docs):
 out=[]
 for d in docs:
  text=d['text']; start=0; n=0
  while start<len(text):
   end=min(start+650,len(text))
   if end<len(text):
    boundary=max(text.rfind('\n',start+250,end),text.rfind('。',start+250,end))
    if boundary>start: end=boundary+1
   out.append({'id':d['id'],'chunk_id':d['id']+':'+str(n),'title':d['title'],'date':d['date'],'version':d.get('version'),'snippet':text[start:end]})
   if end>=len(text): break
   start=max(start+1,end-60); n+=1
 return out

def sparse_vector(text):
 t=tokens(text); count=Counter(t); norm=math.sqrt(sum(v*v for v in count.values())) or 1
 return {k:v/norm for k,v in count.items()}

def cosine(a,b): return sum(x*y for x,y in zip(a,b))/(math.sqrt(sum(x*x for x in a))*math.sqrt(sum(y*y for y in b)) or 1)

_cache={}; _lock=threading.Lock()
def embeddings(texts,budget=None):
 cfg=configuration(); key=cfg.get('RELAY_API_KEY',''); model=cfg.get('EMBEDDING_MODEL','text-embedding-3-large'); base=cfg.get('RELAY_BASE_URL','https://yunwu.ai/v1').rstrip('/')
 if not key: raise RuntimeError('未配置向量模型')
 # Cache keys include a digest of the credential to avoid cross-account reuse; no content or key is logged.
 namespace=hashlib.sha256((base+'|'+model+'|'+key).encode()).hexdigest()
 keys=[(namespace,hashlib.sha256(t.encode()).hexdigest()) for t in texts]
 with _lock: found={k:_cache[k] for k in keys if k in _cache}
 missing=list(dict.fromkeys(k for k in keys if k not in found)); values={k:t for k,t in zip(keys,texts)}
 for pos in range(0,len(missing),32):
  batch=missing[pos:pos+32]; body=json.dumps({'model':model,'input':[values[k] for k in batch]},ensure_ascii=False).encode()
  if budget is not None: budget.embedding([values[k] for k in batch])
  req=urllib.request.Request(base+'/embeddings',data=body,headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'})
  with urllib.request.urlopen(req,timeout=budget.timeout(25) if budget is not None else 25) as response:
   response_data=read_json(response,16*1024*1024,budget)
   if budget is not None: budget.record_usage(response_data.get('usage'))
   data=response_data['data']
  if len(data)!=len(batch): raise RuntimeError('向量接口返回数量不匹配')
  indexed={x['index']:x['embedding'] for x in data}
  if set(indexed)!=set(range(len(batch))): raise RuntimeError('向量响应索引无效')
  for i,k in enumerate(batch):
   v=indexed[i]
   if not isinstance(v,list) or not v or not all(isinstance(x,(int,float)) and math.isfinite(x) for x in v): raise RuntimeError('向量响应无效')
   found[k]=v
  with _lock:
   if len(_cache)+len(batch)>500: _cache.clear()
   _cache.update({k:found[k] for k in batch})
 return [found[k] for k in keys]

def search(query,docs,mode='mock',embedding_fn=None,top_k=8,budget=None):
 parts=chunks(docs); q=tokens(query)
 if not parts or not q: return [],{'vector':'none','reason':'没有可检索的文本'}
 if len(parts)>128: return [],{'vector':'none','reason':'文档分片超过本地检索上限，请缩小知识库范围','limit_exceeded':True,'chunks':len(parts)}
 texts=[x['title']+' '+x['snippet'] for x in parts]; tk=[tokens(t) for t in texts]; df=Counter(t for doc in tk for t in set(doc)); avg=sum(map(len,tk))/len(tk) or 1; bm=[]
 for doc in tk:
  f=Counter(doc); score=0
  for t in set(q):
   if f[t]: score+=math.log(1+(len(parts)-df[t]+.5)/(df[t]+.5))*f[t]*2.5/(f[t]+1.5*(.25+.75*len(doc)/avg))
  bm.append(score)
 kind='local_char_vector'; reason='离线字符向量，只验证流程，不具备语义模型能力'
 if mode=='real' or embedding_fn:
  try:
   vectors=embedding_fn([query]+texts) if embedding_fn else embeddings([query]+texts,budget=budget)
   if len(vectors)!=len(texts)+1 or any(len(v)!=len(vectors[0]) for v in vectors): raise ValueError('向量维度不一致')
   vec=[cosine(vectors[0],v) for v in vectors[1:]]; kind='dense_embedding'; reason='远程语义向量'
  except BudgetExceeded:
   raise
  except Exception:
   vec=None; reason='向量服务不可用，降级为本地字符向量'
 else: vec=None
 if vec is None:
  qv=sparse_vector(query); vec=[]
  for t in texts:
   dv=sparse_vector(t); vec.append(sum(v*dv.get(k,0) for k,v in qv.items()))
 rrf=Counter()
 for scores,threshold in [(bm,0),(vec,.42 if kind=='dense_embedding' else .15)]:
  hits=sorted((i for i,x in enumerate(scores) if x>threshold),key=lambda i:(-scores[i],i))[:20]
  for rank,i in enumerate(hits,1): rrf[i]+=1/(60+rank)
 ranked=sorted(rrf,key=lambda i:(-rrf[i],i)); chosen=[]; seen=set(); deferred=[]
 for i in ranked:
  if parts[i]['id'] in seen: deferred.append(i); continue
  seen.add(parts[i]['id']); chosen.append(parts[i])
  if len(chosen)>=top_k: break
 if len(chosen)<top_k:
  counts=Counter(x['id'] for x in chosen)
  for i in deferred:
   if counts[parts[i]['id']]>=2: continue
   chosen.append(parts[i]); counts[parts[i]['id']]+=1
   if len(chosen)>=top_k: break
 return chosen,{'vector':kind,'reason':reason,'bm25_candidates':sum(x>0 for x in bm),'chunks':len(parts)}
