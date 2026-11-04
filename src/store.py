"""Local SQLite persistence and authorization for the knowledge assistant."""
import hashlib, hmac, json, os, secrets, sqlite3, time
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(os.environ.get('KNOWLEDGE_DB', Path(__file__).resolve().parents[1] / 'data' / 'workspace.db'))

@contextmanager
def connect():
    c=sqlite3.connect(DB_PATH, timeout=10)
    c.row_factory=sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON')
    try:
        with c:
            yield c
    finally:
        c.close()

def password_hash(password, salt=None):
    salt=salt or secrets.token_hex(16)
    return salt+'$'+hashlib.pbkdf2_hmac('sha256',password.encode(),bytes.fromhex(salt),240000).hex()

def verify(password, stored):
    return hmac.compare_digest(password_hash(password,stored.split('$')[0]),stored)

def init():
    with connect() as c:
        c.executescript('''CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, password TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('admin','employee')), department TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, expires INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS documents(id INTEGER PRIMARY KEY, title TEXT NOT NULL, department TEXT NOT NULL, active_version INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS versions(doc_id INTEGER NOT NULL REFERENCES documents(id), version INTEGER NOT NULL, date TEXT NOT NULL, text TEXT NOT NULL, PRIMARY KEY(doc_id,version));
        CREATE TABLE IF NOT EXISTS memory(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), question TEXT NOT NULL, answer TEXT NOT NULL, created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);''')
        if not c.execute('SELECT 1 FROM users LIMIT 1').fetchone():
            c.execute('INSERT INTO users(username,password,role,department) VALUES(?,?,?,?)',('admin',password_hash('admin123'),'admin',''))
        if not c.execute('SELECT 1 FROM documents LIMIT 1').fetchone():
            import json
            seed=json.loads((Path(__file__).resolve().parents[1]/'data/docs.json').read_text(encoding='utf-8'))
            for d in seed:
                cur=c.execute('INSERT INTO documents(title,department) VALUES(?,?)',(d['title'],'公共'))
                c.execute('INSERT INTO versions VALUES(?,?,?,?)',(cur.lastrowid,1,d['date'],d['text']))
        c.executescript('''CREATE TABLE IF NOT EXISTS departments(name TEXT PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS doc_access(doc_id INTEGER NOT NULL REFERENCES documents(id), department TEXT NOT NULL REFERENCES departments(name), PRIMARY KEY(doc_id,department));
        CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);''')
        c.executescript('''CREATE TABLE IF NOT EXISTS conversations(id TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), title TEXT NOT NULL, created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS conversation_turns(id INTEGER PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE, question TEXT NOT NULL, result TEXT NOT NULL, created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS memory_summaries(conversation_id TEXT PRIMARY KEY REFERENCES conversations(id) ON DELETE CASCADE, user_id INTEGER NOT NULL REFERENCES users(id), fingerprint TEXT NOT NULL, summary TEXT NOT NULL, method TEXT NOT NULL, source_count INTEGER NOT NULL, updated TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);''')
        if 'conversation_id' not in {row['name'] for row in c.execute('PRAGMA table_info(memory)')}:
            c.execute('ALTER TABLE memory ADD COLUMN conversation_id TEXT REFERENCES conversations(id)')
        if not c.execute("SELECT 1 FROM metadata WHERE key='departments_migrated'").fetchone():
            for row in c.execute('SELECT department FROM users UNION SELECT department FROM documents'):
                if row[0]: c.execute('INSERT OR IGNORE INTO departments(name) VALUES(?)',(row[0],))
            c.execute("INSERT OR IGNORE INTO departments VALUES('公共')")
            c.execute('INSERT INTO doc_access SELECT id,department FROM documents')
            c.execute("INSERT INTO metadata VALUES('departments_migrated','1')")
        if not c.execute("SELECT 1 FROM metadata WHERE key='admin_department_separation'").fetchone():
            # The previous bootstrap created a synthetic 管理 department for admin.
            # Keep it if anyone else or any document uses it as a real department.
            uses = c.execute("SELECT (SELECT count(*) FROM users WHERE department='管理' AND username!='admin') + (SELECT count(*) FROM documents WHERE department='管理') + (SELECT count(*) FROM doc_access WHERE department='管理')").fetchone()[0]
            if not uses:
                c.execute("UPDATE users SET department='' WHERE username='admin' AND role='admin' AND department='管理'")
            c.execute("INSERT INTO metadata VALUES('admin_department_separation','1')")

def departments():
    with connect() as c: return [r['name'] for r in c.execute('SELECT name FROM departments ORDER BY name')]

def add_departments(names):
    if not isinstance(names,list) or not names or len(names)>500: raise ValueError('请提供 1 至 500 个部门名称')
    if any(not isinstance(x,str) or not x.strip() or len(x.strip())>60 for x in names): raise ValueError('部门名称不能为空且最多 60 字')
    clean=list(dict.fromkeys(x.strip() for x in names))
    with connect() as c:
        for name in clean: c.execute('INSERT OR IGNORE INTO departments VALUES(?)',(name,))
    return {'count':len(clean)}

def department_rename_plan(c,old,new):
    if not isinstance(old,str) or not isinstance(new,str) or not new.strip() or len(new.strip())>60: raise ValueError('部门名称无效')
    old=old.strip(); new=new.strip()
    if old=='公共' or new=='公共': raise ValueError('公共部门不可重命名')
    if old==new: raise ValueError('新旧部门名称相同')
    if not c.execute('SELECT 1 FROM departments WHERE name=?',(old,)).fetchone(): raise ValueError('原部门不存在')
    if c.execute('SELECT 1 FROM departments WHERE name=?',(new,)).fetchone(): raise ValueError('新部门名称已存在，请另选名称；不支持自动合并部门')
    users=[dict(x) for x in c.execute('SELECT id,username,role FROM users WHERE department=? ORDER BY id',(old,))]
    owned=[dict(x) for x in c.execute('SELECT id,title FROM documents WHERE department=? ORDER BY id',(old,))]
    access=[dict(x) for x in c.execute('SELECT d.id,d.title FROM doc_access a JOIN documents d ON d.id=a.doc_id WHERE a.department=? ORDER BY d.id',(old,))]
    plan={'old':old,'new':new,'users':users,'owned_documents':owned,'access_documents':access}
    plan['signature']=hashlib.sha256(json.dumps(plan,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    return plan

def preview_department_rename(old,new):
    with connect() as c: return department_rename_plan(c,old,new)

def rename_department(old,new,expected_signature=None):
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        plan=department_rename_plan(c,old,new)
        if expected_signature is not None and plan['signature']!=expected_signature: raise ValueError('部门关联数据已变化，请重新预览')
        old=plan['old']; new=plan['new']
        c.execute('INSERT INTO departments VALUES(?)',(new,))
        c.execute('UPDATE users SET department=? WHERE department=?',(new,old))
        c.execute('UPDATE documents SET department=? WHERE department=?',(new,old))
        c.execute('UPDATE doc_access SET department=? WHERE department=?',(new,old))
        c.execute('DELETE FROM departments WHERE name=?',(old,))

def bulk_create_users(rows):
    if not isinstance(rows,list) or not rows or len(rows)>200: raise ValueError('请提供 1 至 200 名员工')
    with connect() as c:
        known={r[0] for r in c.execute('SELECT name FROM departments')}
        names=set()
        for row in rows:
            if not isinstance(row,dict): raise ValueError('员工数据格式无效')
            username=row.get('username','').strip(); password=row.get('password',''); dept=row.get('department','').strip()
            if not username or username in names or not isinstance(password,str) or len(password)<8 or dept not in known or dept=='公共': raise ValueError('用户名重复、密码不足 8 位或部门不存在')
            names.add(username)
            if c.execute('SELECT 1 FROM users WHERE username=?',(username,)).fetchone(): raise ValueError('用户名已存在：'+username)
        for row in rows: c.execute('INSERT INTO users(username,password,role,department) VALUES(?,?,?,?)',(row['username'].strip(),password_hash(row['password']),'employee',row['department'].strip()))
    return {'count':len(rows)}

def user_change_plan(c,ids,department=None,role=None):
    if not isinstance(ids,list) or not ids or len(ids)>200 or any(type(x)!=int for x in ids) or len(set(ids))!=len(ids): raise ValueError('请选择 1 至 200 名员工')
    if department is None and role is None: raise ValueError('请至少选择一项变更')
    if department is not None and (not isinstance(department,str) or not department.strip() or department=='公共' or not c.execute('SELECT 1 FROM departments WHERE name=?',(department,)).fetchone()): raise ValueError('目标部门不存在')
    if role is not None and role not in ('employee','admin'): raise ValueError('目标身份无效')
    docs=[dict(x) for x in c.execute('SELECT id,title,department FROM documents ORDER BY id')]
    grants=[dict(x) for x in c.execute('SELECT doc_id,department FROM doc_access ORDER BY doc_id,department')]
    def permitted(dept,identity):
        if identity=='admin': return {x['id'] for x in docs}
        return {x['doc_id'] for x in grants if x['department'] in ('公共',dept)}
    result=[]
    for uid in sorted(ids):
        row=c.execute('SELECT id,username,role,department FROM users WHERE id=?',(uid,)).fetchone()
        if not row or row['role']!='employee': raise ValueError('仅可修改当前为员工的账号；管理员账号不在操作范围')
        before=dict(row); new_dept=department if department is not None else row['department']; new_role=role if role is not None else row['role']
        old_access=permitted(row['department'],row['role']); new_access=permitted(new_dept,new_role)
        result.append({'id':uid,'username':row['username'],'before':{'department':row['department'],'role':row['role']},'after':{'department':new_dept,'role':new_role},'gained':[{'id':'D'+str(d['id']),'title':d['title']} for d in docs if d['id'] in new_access-old_access],'lost':[{'id':'D'+str(d['id']),'title':d['title']} for d in docs if d['id'] in old_access-new_access]})
    if all(item['before']==item['after'] for item in result): raise ValueError('所选员工已处于目标状态，无需变更')
    plan={'users':result,'department':department,'role':role}
    plan['signature']=hashlib.sha256(json.dumps({'plan':plan,'docs':docs,'grants':grants},ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    return plan

def preview_user_changes(ids,department=None,role=None):
    with connect() as c: return user_change_plan(c,ids,department,role)

def bulk_update_users(ids,department=None,role=None,expected_signature=None):
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        plan=user_change_plan(c,ids,department,role)
        if expected_signature is not None and plan['signature']!=expected_signature: raise ValueError('员工或文档权限已变化，请重新预览')
        for row in plan['users']:
            c.execute('UPDATE users SET department=?,role=? WHERE id=?',(row['after']['department'],row['after']['role'],row['id']))
    return {'count':len(ids)}

def set_doc_access(ids,departments):
    if not isinstance(ids,list) or not ids or len(ids)>200 or len(ids)!=len(set(ids)): raise ValueError('请选择文档')
    if not isinstance(departments,list) or not departments or len(departments)!=len(set(departments)): raise ValueError('请选择可访问部门')
    with connect() as c:
        known={r[0] for r in c.execute('SELECT name FROM departments')}
        if not set(departments)<=known: raise ValueError('部门不存在')
        for doc_id in ids:
            if not c.execute('SELECT 1 FROM documents WHERE id=?',(doc_id,)).fetchone(): raise ValueError('文档不存在')
        for doc_id in ids:
            c.execute('DELETE FROM doc_access WHERE doc_id=?',(doc_id,))
            c.executemany('INSERT INTO doc_access VALUES(?,?)',[(doc_id,d) for d in departments])
    return {'count':len(ids)}

def login(username,password):
    with connect() as c:
        row=c.execute('SELECT * FROM users WHERE username=?',(username,)).fetchone()
        if not row or not verify(password,row['password']): return None
        token=secrets.token_urlsafe(32); c.execute('INSERT INTO sessions VALUES(?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),row['id'],int(time.time())+43200)); return token

def identity(token):
    if not token: return None
    with connect() as c:
        row=c.execute('SELECT u.id,u.username,u.role,u.department FROM users u JOIN sessions s ON s.user_id=u.id WHERE s.token_hash=? AND s.expires>?',(hashlib.sha256(token.encode()).hexdigest(),int(time.time()))).fetchone()
        return dict(row) if row else None

def logout(token):
    with connect() as c: c.execute('DELETE FROM sessions WHERE token_hash=?',(hashlib.sha256(token.encode()).hexdigest(),))

def users():
    with connect() as c: return [dict(x) for x in c.execute('SELECT id,username,role,department FROM users ORDER BY id')]

def create_user(username,password,role,department):
    if not username.strip() or len(password)<8 or role not in ('admin','employee') or not department.strip(): raise ValueError('请填写用户名、部门和至少 8 位密码')
    with connect() as c: c.execute('INSERT INTO users(username,password,role,department) VALUES(?,?,?,?)',(username.strip(),password_hash(password),role,department.strip()))

def update_user(uid, role, department):
    if role not in ('admin','employee') or not department.strip(): raise ValueError('角色或部门无效')
    with connect() as c:
        row=c.execute('SELECT role FROM users WHERE id=?',(uid,)).fetchone()
        if not row: raise ValueError('用户不存在')
        if row['role']=='admin': raise ValueError('不能修改管理员身份')
        if department=='公共' or not c.execute('SELECT 1 FROM departments WHERE name=?',(department,)).fetchone(): raise ValueError('部门不存在')
        c.execute('UPDATE users SET role=?,department=? WHERE id=?',(role,department.strip(),uid))

def documents(user):
    with connect() as c:
        sql='SELECT d.id,d.title,d.department,d.active_version,v.date,v.text FROM documents d JOIN versions v ON v.doc_id=d.id AND v.version=d.active_version'
        rows=c.execute(sql if user['role']=='admin' else sql+' WHERE EXISTS (SELECT 1 FROM doc_access a WHERE a.doc_id=d.id AND a.department IN (?,?))',() if user['role']=='admin' else ('公共',user['department'])).fetchall()
        return [{'id':'D'+str(x['id']),'title':x['title'],'department':x['department'],'access':[r[0] for r in c.execute('SELECT department FROM doc_access WHERE doc_id=? ORDER BY department',(x['id'],))],'version':x['active_version'],'date':x['date'],'text':x['text']} for x in rows]

def add_document(title,department,date,text,doc_id=None,access=None):
    if not all(isinstance(x,str) and x.strip() for x in (title,department,date,text)): raise ValueError('请完整填写标题、部门、日期和正文')
    if len(text)>200000: raise ValueError('文档超过 200000 字符限制')
    from datetime import date as dt
    try: dt.fromisoformat(date)
    except ValueError: raise ValueError('日期必须为 YYYY-MM-DD')
    with connect() as c:
        if access is not None:
            if not isinstance(access,list) or not access or len(set(access))!=len(access): raise ValueError('请选择可访问部门')
            known={r[0] for r in c.execute('SELECT name FROM departments')}
            if department not in known or not set(access)<=known: raise ValueError('部门不存在')
        if doc_id:
            doc=c.execute('SELECT * FROM documents WHERE id=?',(doc_id,)).fetchone()
            if not doc: raise ValueError('文档不存在')
            if (title,department)!=(doc['title'],doc['department']): raise ValueError('新版本必须保持原文档标题和部门')
            version=c.execute('SELECT COALESCE(MAX(version),0)+1 FROM versions WHERE doc_id=?',(doc_id,)).fetchone()[0]
            c.execute('UPDATE documents SET active_version=? WHERE id=?',(version,doc_id))
        else:
            cur=c.execute('INSERT INTO documents(title,department) VALUES(?,?)',(title,department)); doc_id=cur.lastrowid; version=1
            c.execute('INSERT OR IGNORE INTO departments VALUES(?)',(department,))
            c.executemany('INSERT INTO doc_access VALUES(?,?)',[(doc_id,d) for d in (access if access is not None else [department])])
        if access is not None and version>1:
            c.execute('DELETE FROM doc_access WHERE doc_id=?',(doc_id,))
            c.executemany('INSERT INTO doc_access VALUES(?,?)',[(doc_id,d) for d in access])
        c.execute('INSERT INTO versions VALUES(?,?,?,?)',(doc_id,version,date,text))
        return {'id':doc_id,'version':version}

def versions(doc_id):
    with connect() as c: return [dict(x) for x in c.execute('SELECT version,date,text FROM versions WHERE doc_id=? ORDER BY version DESC',(doc_id,))]

def activate(doc_id,version):
    with connect() as c:
        if not c.execute('SELECT 1 FROM versions WHERE doc_id=? AND version=?',(doc_id,version)).fetchone(): raise ValueError('版本不存在')
        c.execute('UPDATE documents SET active_version=? WHERE id=?',(version,doc_id))

def memories(uid,offset=0):
    with connect() as c: return [dict(x) for x in c.execute("SELECT id,question,CASE WHEN conversation_id IS NOT NULL THEN '答案请在对应会话中查看。' ELSE answer END AS answer,created FROM memory WHERE user_id=? ORDER BY id DESC LIMIT 50 OFFSET ?",(uid,offset))]

def remember(uid,q,a,conversation_id=None):
    with connect() as c: c.execute('INSERT INTO memory(user_id,question,answer,conversation_id) VALUES(?,?,?,?)',(uid,q,a,conversation_id))

def create_conversation(uid):
    conv_id=secrets.token_urlsafe(18)
    with connect() as c: c.execute('INSERT INTO conversations(id,user_id,title) VALUES(?,?,?)',(conv_id,uid,'新会话'))
    return {'id':conv_id,'title':'新会话'}

def conversations(uid):
    with connect() as c: return [dict(row) for row in c.execute('SELECT id,title,created,updated FROM conversations WHERE user_id=? ORDER BY updated DESC,rowid DESC',(uid,))]

def owns_conversation(uid,conv_id):
    with connect() as c: return bool(c.execute('SELECT 1 FROM conversations WHERE id=? AND user_id=?',(conv_id,uid)).fetchone())

def conversation_history(uid,conv_id):
    with connect() as c:
        if not c.execute('SELECT 1 FROM conversations WHERE id=? AND user_id=?',(conv_id,uid)).fetchone(): raise ValueError('会话不存在')
        return [r['question'] for r in reversed(c.execute('SELECT question FROM memory WHERE user_id=? AND conversation_id=? ORDER BY id DESC LIMIT 10',(uid,conv_id)).fetchall())]

def memory_items(uid,conv_id):
    with connect() as c:
        if not c.execute('SELECT 1 FROM conversations WHERE id=? AND user_id=?',(conv_id,uid)).fetchone(): raise ValueError('会话不存在')
        return [dict(r) for r in c.execute('SELECT id,question FROM memory WHERE user_id=? AND conversation_id=? ORDER BY id',(uid,conv_id))]

def get_summary(uid,conv_id):
    with connect() as c:
        if not c.execute('SELECT 1 FROM conversations WHERE id=? AND user_id=?',(conv_id,uid)).fetchone(): raise ValueError('会话不存在')
        row=c.execute('SELECT fingerprint,summary,method,source_count,updated FROM memory_summaries WHERE user_id=? AND conversation_id=?',(uid,conv_id)).fetchone()
        return dict(row) if row else None

def summaries(uid):
    with connect() as c:
        return [dict(r) for r in c.execute('SELECT s.conversation_id,s.summary,s.method,s.source_count,s.updated,c.title FROM memory_summaries s JOIN conversations c ON c.id=s.conversation_id WHERE s.user_id=? AND c.user_id=? ORDER BY s.updated DESC',(uid,uid))]

def clear_summary(uid,conv_id):
    with connect() as c: c.execute('DELETE FROM memory_summaries WHERE user_id=? AND conversation_id=?',(uid,conv_id))

def forget_conversation(uid,conv_id):
    with connect() as c:
        if not c.execute('SELECT 1 FROM conversations WHERE id=? AND user_id=?',(conv_id,uid)).fetchone(): raise ValueError('会话不存在')
        c.execute('DELETE FROM memory WHERE user_id=? AND conversation_id=?',(uid,conv_id))
        c.execute('DELETE FROM memory_summaries WHERE user_id=? AND conversation_id=?',(uid,conv_id))

def save_summary(uid,conv_id,all_items,fingerprint,summary,method,source_count):
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        current=[dict(r) for r in c.execute('SELECT id,question FROM memory WHERE user_id=? AND conversation_id=? ORDER BY id',(uid,conv_id))]
        if current!=all_items: return False
        c.execute('INSERT INTO memory_summaries(conversation_id,user_id,fingerprint,summary,method,source_count) VALUES(?,?,?,?,?,?) ON CONFLICT(conversation_id) DO UPDATE SET fingerprint=excluded.fingerprint,summary=excluded.summary,method=excluded.method,source_count=excluded.source_count,updated=CURRENT_TIMESTAMP',(conv_id,uid,fingerprint,summary,method,source_count))
        return True

def save_turn(uid,conv_id,question,result):
    with connect() as c:
        if not c.execute('SELECT 1 FROM conversations WHERE id=? AND user_id=?',(conv_id,uid)).fetchone(): raise ValueError('会话不存在')
        c.execute('INSERT INTO conversation_turns(conversation_id,question,result) VALUES(?,?,?)',(conv_id,question,json.dumps(result,ensure_ascii=False)))
        c.execute("UPDATE conversations SET title=CASE WHEN title='新会话' THEN ? ELSE title END, updated=CURRENT_TIMESTAMP WHERE id=?",(question[:30],conv_id))
        if result.get('status') not in ('invalid_input','model_error','tool_error','security_blocked'):
            c.execute('INSERT INTO memory(user_id,question,answer,conversation_id) VALUES(?,?,?,?)',(uid,question,result['answer'],conv_id))

def conversation_turns(user,conv_id):
    with connect() as c:
        if not c.execute('SELECT 1 FROM conversations WHERE id=? AND user_id=?',(conv_id,user['id'])).fetchone(): raise ValueError('会话不存在')
        rows=c.execute('SELECT question,result,created FROM conversation_turns WHERE conversation_id=? ORDER BY id',(conv_id,)).fetchall()
    allowed={d['id'] for d in documents(user)}
    output=[]
    for row in rows:
        result=json.loads(row['result'])
        referenced={x.get('id') for x in result.get('citations',[]) if isinstance(x,dict) and x.get('id')}
        for step in result.get('trace',[]):
            if isinstance(step.get('result'),list): referenced.update(x.get('id') for x in step['result'] if isinstance(x,dict) and x.get('id'))
        if referenced - allowed:
            result={'status':'access_changed','answer':'该回答引用的文档当前不可访问，内容已隐藏。','citations':[],'gaps':['文档访问权限已变化'],'trace':[]}
        output.append({'question':row['question'],'result':result,'created':row['created']})
    return output

def forget(uid,mid=None):
    with connect() as c:
        if mid is None:
            c.execute('DELETE FROM memory WHERE user_id=?',(uid,))
            c.execute('DELETE FROM memory_summaries WHERE user_id=?',(uid,))
        else:
            row=c.execute('SELECT conversation_id FROM memory WHERE user_id=? AND id=?',(uid,mid)).fetchone()
            c.execute('DELETE FROM memory WHERE user_id=? AND id=?',(uid,mid))
            if row and row['conversation_id']:
                c.execute('DELETE FROM memory_summaries WHERE user_id=? AND conversation_id=?',(uid,row['conversation_id']))
def change_password(uid, old, new):
    if len(new)<8: raise ValueError('新密码至少 8 位')
    with connect() as c:
        row=c.execute('SELECT password FROM users WHERE id=?',(uid,)).fetchone()
        if not row or not verify(old,row['password']): raise ValueError('原密码错误')
        c.execute('UPDATE users SET password=? WHERE id=?',(password_hash(new),uid))
        c.execute('DELETE FROM sessions WHERE user_id=?',(uid,))
