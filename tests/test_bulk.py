import sys,unittest,tempfile
from pathlib import Path
sys.path.insert(0,'src')
import store
class BulkTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(); self.old=store.DB_PATH; store.DB_PATH=Path(self.tmp.name)/'test.db'; store.init(); store.add_departments(['财务','人事','行政'])
 def tearDown(self): store.DB_PATH=self.old; self.tmp.cleanup()
 def test_import_atomic_and_admin_protected(self):
  rows=[{'username':'a','password':'password1','department':'财务'},{'username':'b','password':'password2','department':'不存在'}]
  with self.assertRaises(ValueError): store.bulk_create_users(rows)
  self.assertNotIn('a',[x['username'] for x in store.users()]); rows[1]['department']='人事'; store.bulk_create_users(rows)
  staff=[x['id'] for x in store.users() if x['role']=='employee']; admin=next(x['id'] for x in store.users() if x['role']=='admin')
  with self.assertRaises(ValueError): store.bulk_update_users(staff+[admin],'行政')
  self.assertEqual(next(x for x in store.users() if x['username']=='a')['department'],'财务')
  store.bulk_update_users(staff,'行政'); self.assertTrue(all(x['department']=='行政' for x in store.users() if x['role']=='employee'))
 def test_multi_department_access_and_atomic(self):
  d=store.add_document('预算','财务','2026-01-01','预算需要审批。',access=['财务','人事']); id=d['id']
  self.assertTrue(any(x['id']==f'D{id}' for x in store.documents({'role':'employee','department':'人事'})))
  self.assertFalse(any(x['id']==f'D{id}' for x in store.documents({'role':'employee','department':'行政'})))
  with self.assertRaises(ValueError): store.set_doc_access([id,99999],['行政'])
  self.assertTrue(any(x['id']==f'D{id}' for x in store.documents({'role':'employee','department':'人事'})))
  store.set_doc_access([id],['行政']); self.assertFalse(any(x['id']==f'D{id}' for x in store.documents({'role':'employee','department':'人事'})))
  self.assertTrue(any(x['id']==f'D{id}' for x in store.documents({'role':'employee','department':'行政'})))
 def test_migration_idempotent(self):
  before=store.documents({'role':'admin','department':'管理'}); store.init(); after=store.documents({'role':'admin','department':'管理'}); self.assertEqual(before,after)
 def test_default_admin_role_not_department(self):
  admin=store.identity(store.login('admin','admin123')); self.assertEqual(admin['role'],'admin'); self.assertEqual(admin['department'],'')
  self.assertNotIn('管理',store.departments())
 def test_rename_carries_user_and_access(self):
  store.bulk_create_users([{'username':'a','password':'password1','department':'财务'}])
  doc=store.add_document('预算','财务','2026-01-01','预算审批。',access=['财务','人事'])
  store.rename_department('财务','财务中心')
  user=store.identity(store.login('a','password1')); self.assertEqual(user['department'],'财务中心')
  found=next(x for x in store.documents(user) if x['id']==f"D{doc['id']}")
  self.assertEqual(found['department'],'财务中心'); self.assertIn('财务中心',found['access']); self.assertNotIn('财务',found['access'])
 def test_transfer_changes_only_department_and_access(self):
  store.bulk_create_users([{'username':'a','password':'password1','department':'财务'},{'username':'b','password':'password2','department':'人事'}])
  a=next(x for x in store.users() if x['username']=='a'); token=store.login('a','password1')
  old=store.add_document('财务私有','财务','2026-01-01','财务内容',access=['财务'])
  new=store.add_document('人事私有','人事','2026-01-01','人事内容',access=['人事'])
  plan=store.preview_user_changes([a['id']],'人事',None); item=plan['users'][0]
  self.assertEqual(item['after']['role'],'employee'); self.assertEqual([x['id'] for x in item['lost']],[f"D{old['id']}"]); self.assertEqual([x['id'] for x in item['gained']],[f"D{new['id']}"])
  store.bulk_update_users([a['id']],'人事',None,plan['signature'])
  self.assertEqual(store.identity(token)['department'],'人事'); self.assertEqual(store.identity(token)['role'],'employee')
  visible={x['id'] for x in store.documents(store.identity(token))}; self.assertNotIn(f"D{old['id']}",visible); self.assertIn(f"D{new['id']}",visible)
  b=next(x for x in store.users() if x['username']=='b'); self.assertEqual(b['department'],'人事')
 def test_role_only_and_stale_preview(self):
  store.bulk_create_users([{'username':'a','password':'password1','department':'财务'}]); a=next(x for x in store.users() if x['username']=='a')
  plan=store.preview_user_changes([a['id']],None,'admin'); self.assertEqual(plan['users'][0]['after']['department'],'财务')
  store.add_document('变更','财务','2026-01-01','新内容',access=['财务'])
  with self.assertRaisesRegex(ValueError,'重新预览'): store.bulk_update_users([a['id']],None,'admin',plan['signature'])
  self.assertEqual(next(x for x in store.users() if x['id']==a['id'])['role'],'employee')
  plan=store.preview_user_changes([a['id']],None,'admin'); store.bulk_update_users([a['id']],None,'admin',plan['signature'])
  actual=next(x for x in store.users() if x['id']==a['id']); self.assertEqual((actual['department'],actual['role']),('财务','admin'))
  with self.assertRaises(ValueError): store.preview_user_changes([a['id']],'人事',None)
 def test_rename_preview_collision_and_stale(self):
  store.bulk_create_users([{'username':'a','password':'password1','department':'财务'}])
  d=store.add_document('财务制度','财务','2026-01-01','正文',access=['财务','人事']); store.add_document('人事制度','人事','2026-01-01','正文',access=['财务'])
  plan=store.preview_department_rename('财务','财务中心'); self.assertEqual((len(plan['users']),len(plan['owned_documents']),len(plan['access_documents'])),(1,1,2))
  with self.assertRaisesRegex(ValueError,'不支持自动合并'): store.preview_department_rename('财务','人事')
  with self.assertRaises(ValueError): store.preview_department_rename('公共','其它')
  store.set_doc_access([d['id']],['人事'])
  with self.assertRaisesRegex(ValueError,'重新预览'): store.rename_department('财务','财务中心',plan['signature'])
  self.assertIn('财务',store.departments()); self.assertNotIn('财务中心',store.departments())
  plan=store.preview_department_rename('财务','财务中心'); store.rename_department('财务','财务中心',plan['signature'])
  self.assertEqual(next(x for x in store.users() if x['username']=='a')['department'],'财务中心')
  self.assertEqual(store.versions(d['id'])[0]['text'],'正文')
