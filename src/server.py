from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import urlparse, parse_qs
import json, sqlite3
import store
import memory
from guardrails import Budget, BudgetExceeded, suspicious
from agent import run
from word import extract_docx

class Handler(BaseHTTPRequestHandler):
 def send_json(self,status,data,cookie=None):
  raw=json.dumps(data,ensure_ascii=False).encode(); self.send_response(status); self.send_header('Content-Type','application/json; charset=utf-8'); self.send_header('Cache-Control','no-store'); self.send_header('X-Content-Type-Options','nosniff')
  if cookie: self.send_header('Set-Cookie',cookie)
  self.end_headers(); self.wfile.write(raw)
 def token(self):
  c=SimpleCookie()
  try: c.load(self.headers.get('Cookie','')); return c['session'].value if 'session' in c else ''
  except Exception: return ''
 def do_GET(self):
  path=urlparse(self.path).path
  if path=='/':
   data=(Path(__file__).parent/'index.html').read_bytes(); self.send_response(200); self.send_header('Content-Type','text/html; charset=utf-8'); self.end_headers(); self.wfile.write(data); return
  user=store.identity(self.token())
  if not user: return self.send_json(401,{'error':'请先登录'})
  if path=='/api/me': return self.send_json(200,user)
  if path=='/api/documents': return self.send_json(200,store.documents(user))
  if path=='/api/memory':
   try:
    offset=int(parse_qs(urlparse(self.path).query).get('offset',['0'])[0])
    if offset<0: raise ValueError()
    return self.send_json(200,store.memories(user['id'],offset))
   except ValueError: return self.send_json(400,{'error':'分页参数无效'})
  if path=='/api/memory/summaries': return self.send_json(200,store.summaries(user['id']))
  if path=='/api/conversations': return self.send_json(200,store.conversations(user['id']))
  if path=='/api/conversation':
   conv_id=parse_qs(urlparse(self.path).query).get('id',[''])[0]
   try: return self.send_json(200,store.conversation_turns(user,conv_id))
   except ValueError: return self.send_json(404,{'error':'会话不存在'})
  if user['role']!='admin': return self.send_json(403,{'error':'需要管理员权限'})
  if path=='/api/users': return self.send_json(200,store.users())
  if path=='/api/departments': return self.send_json(200,store.departments())
  if path=='/api/versions':
   try: return self.send_json(200,store.versions(int(parse_qs(urlparse(self.path).query)['id'][0])))
   except (ValueError,KeyError,IndexError): return self.send_json(400,{'error':'文档 ID 无效'})
  return self.send_json(404,{'error':'接口不存在'})
 def do_POST(self):
  path=urlparse(self.path).path
  origin=self.headers.get('Origin')
  if origin and origin not in ('http://127.0.0.1:8000','http://localhost:8000'): return self.send_json(403,{'error':'来源不允许'})
  if self.headers.get_content_type()!='application/json': return self.send_json(415,{'error':'需要 JSON'})
  try:
   length=int(self.headers.get('Content-Length','0'))
   limit=5700000 if path=='/api/documents/parse-word' else 500000
   if length<=0 or length>limit: return self.send_json(413,{'error':'请求大小无效'})
   data=json.loads(self.rfile.read(length));
   if not isinstance(data,dict): raise ValueError('请求必须为对象')
   if path=='/api/login':
    token=store.login(data.get('username',''),data.get('password',''))
    return self.send_json(200,{'ok':True},f'session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=43200') if token else self.send_json(401,{'error':'用户名或密码错误'})
   user=store.identity(self.token())
   if not user: return self.send_json(401,{'error':'请先登录'})
   if path=='/api/logout':
    store.logout(self.token()); return self.send_json(200,{'ok':True},'session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0')
   if path=='/api/password':
    store.change_password(user['id'],data.get('old',''),data.get('new','')); return self.send_json(200,{'ok':True},'session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0')
   if path=='/api/conversations/create': return self.send_json(200,store.create_conversation(user['id']))
   if path=='/api/ask':
    budget=Budget()
    conv_id=data.get('conversation_id')
    if conv_id is not None and (not isinstance(conv_id,str) or not store.owns_conversation(user['id'],conv_id)): return self.send_json(404,{'error':'会话不存在'})
    docs=store.documents(user)
    # Each conversation uses only its own undeleted memory.
    question=data.get('question',''); mode=data.get('mode','mock')
    result=None
    if conv_id and isinstance(question,str) and question.strip() and len(question)<=3000 and mode in ('mock','real') and not suspicious(question):
     try: context=memory.build(user['id'],conv_id,mode,budget=budget)
     except BudgetExceeded as exc:
      context={'history':[],'summary':'','method':'budget_exceeded','source_count':0,'note':str(exc)}
      result={'status':'incomplete','answer':str(exc)+'，任务未完成。','citations':[],'gaps':[str(exc)],'trace':[],'mode':mode,'budget':budget.report()}
    else:
     context={'history':[x['question'] for x in reversed(store.memories(user['id'])[:10])] if not conv_id else [],'summary':'','method':'none','source_count':0,'note':'无摘要'}
    if result is None: result=run(question,history=context['history'],memory_summary=context['summary'],mode=mode,docs=docs,budget=budget)
    result['memory']={k:context[k] for k in ('method','source_count','note')}
    latest_user=store.identity(self.token())
    if not latest_user: return self.send_json(401,{'error':'登录已失效'})
    latest_ids={d['id'] for d in store.documents(latest_user)}
    if {d['id'] for d in docs}-latest_ids:
     result={'status':'access_changed','answer':'问答期间访问权限发生变化，请重新提问。','citations':[],'gaps':['需要按当前权限重新检索'],'trace':[]}
    if result['status']!='invalid_input' and conv_id:
     store.save_turn(user['id'],conv_id,data['question'],result)
    elif result['status'] not in ('invalid_input','model_error','tool_error','security_blocked'): store.remember(user['id'],data['question'],result['answer'])
    return self.send_json(200,result)
   if path=='/api/memory/delete': store.forget(user['id'],data.get('id')); return self.send_json(200,{'ok':True})
   if path=='/api/memory/conversation/clear': store.forget_conversation(user['id'],data.get('conversation_id')); return self.send_json(200,{'ok':True})
   if path=='/api/memory/summary/delete': store.clear_summary(user['id'],data.get('conversation_id')); return self.send_json(200,{'ok':True})
   if user['role']!='admin': return self.send_json(403,{'error':'需要管理员权限'})
   if path=='/api/documents/parse-word': return self.send_json(200,{'text':extract_docx(data.get('content'))})
   if path=='/api/departments/import': return self.send_json(200,store.add_departments(data.get('names')))
   if path=='/api/departments/rename-preview': return self.send_json(200,store.preview_department_rename(data.get('old'),data.get('new')))
   if path=='/api/departments/rename':
    if not isinstance(data.get('signature'),str): raise ValueError('请先预览部门更名影响')
    store.rename_department(data.get('old'),data.get('new'),data['signature']); return self.send_json(200,{'ok':True})
   if path=='/api/users/import': return self.send_json(200,store.bulk_create_users(data.get('rows')))
   if path=='/api/users/change-preview': return self.send_json(200,store.preview_user_changes(data.get('ids'),data.get('department'),data.get('role')))
   if path=='/api/users/bulk-update':
    if not isinstance(data.get('signature'),str): raise ValueError('请先预览员工变更')
    return self.send_json(200,store.bulk_update_users(data.get('ids'),data.get('department'),data.get('role'),data['signature']))
   if path=='/api/documents/bulk-access': return self.send_json(200,store.set_doc_access(data.get('ids'),data.get('departments')))
   if path=='/api/users/create': return self.send_json(200,store.bulk_create_users([data]))
   if path=='/api/users/update': return self.send_json(400,{'error':'请先预览变更，再通过批量变更接口提交'})
   if path=='/api/documents/create': return self.send_json(200,store.add_document(data.get('title',''),data.get('department',''),data.get('date',''),data.get('text',''),data.get('id'),data.get('access',[])))
   if path=='/api/versions/activate': store.activate(int(data['id']),int(data['version'])); return self.send_json(200,{'ok':True})
   return self.send_json(404,{'error':'接口不存在'})
  except (ValueError, KeyError, TypeError, sqlite3.IntegrityError, json.JSONDecodeError) as e: return self.send_json(400,{'error':str(e) if not isinstance(e,sqlite3.IntegrityError) else '数据冲突或用户名已存在'})
 def log_message(self,fmt,*args): pass
if __name__=='__main__':
 store.init(); print('http://127.0.0.1:8000'); ThreadingHTTPServer(('127.0.0.1',8000),Handler).serve_forever()
