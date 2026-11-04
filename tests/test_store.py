import sys,unittest,tempfile
from pathlib import Path
sys.path.insert(0,'src')
import store
from agent import run

class AccessTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(); self.old=store.DB_PATH; store.DB_PATH=Path(self.tmp.name)/'test.db'; store.init(); store.add_departments(['财务','人事'])
 def tearDown(self): store.DB_PATH=self.old; self.tmp.cleanup()
 def test_identity_and_department_isolation(self):
  store.create_user('alice','password1','employee','财务'); store.create_user('bob','password2','employee','人事')
  store.add_document('预算审批','财务','2026-01-01','预算审批需要财务负责人签字。')
  a=store.identity(store.login('alice','password1')); b=store.identity(store.login('bob','password2'))
  self.assertTrue(any(d['title']=='预算审批' for d in store.documents(a))); self.assertFalse(any(d['title']=='预算审批' for d in store.documents(b)))
  self.assertEqual(run('预算审批',docs=store.documents(b))['status'],'insufficient')
  self.assertIn('预算审批',run('预算审批',docs=store.documents(a))['answer'])
  store.update_user(a['id'],'employee','人事'); self.assertFalse(any(d['title']=='预算审批' for d in store.documents(store.identity(store.login('alice','password1')))))
 def test_version_restore(self):
  d=store.add_document('财务报销','财务','2026-01-01','报销期限为40天。'); store.add_document('财务报销','财务','2026-02-01','报销期限为50天。',d['id']); u={'role':'admin','department':'管理'}
  self.assertTrue(any(x['text']=='报销期限为50天。' for x in store.documents(u)))
  store.activate(d['id'],1); self.assertTrue(any(x['text']=='报销期限为40天。' for x in store.documents(u))); self.assertEqual(len(store.versions(d['id'])),2)
 def test_memory_isolation_and_delete(self):
  store.create_user('a','password1','employee','财务'); store.create_user('b','password2','employee','人事'); a=store.identity(store.login('a','password1')); b=store.identity(store.login('b','password2'))
  store.remember(a['id'],'我的问题','答案'); m=store.memories(a['id'])[0]; self.assertEqual(store.memories(b['id']),[]); store.forget(b['id'],m['id']); self.assertEqual(len(store.memories(a['id'])),1); store.forget(a['id']); self.assertEqual(store.memories(a['id']),[])
 def test_password_sessions_and_admin(self):
  token=store.login('admin','admin123'); self.assertIsNotNone(token); admin=store.identity(token)
  with self.assertRaises(ValueError): store.update_user(admin['id'],'employee','管理')
  store.change_password(admin['id'],'admin123','newpassword'); self.assertIsNone(store.identity(token)); self.assertIsNone(store.login('admin','admin123')); self.assertIsNotNone(store.login('admin','newpassword'))
