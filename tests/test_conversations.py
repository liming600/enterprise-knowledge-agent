import sys,unittest,tempfile
from pathlib import Path
sys.path.insert(0,'src')
import store
from agent import run
class ConversationTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(); self.old=store.DB_PATH; store.DB_PATH=Path(self.tmp.name)/'test.db'; store.init(); store.add_departments(['财务','人事']); store.bulk_create_users([{'username':'a','password':'password1','department':'财务'},{'username':'b','password':'password2','department':'人事'}]); self.a=store.identity(store.login('a','password1')); self.b=store.identity(store.login('b','password2'))
 def tearDown(self): store.DB_PATH=self.old; self.tmp.cleanup()
 def test_context_isolation_and_memory_deletion(self):
  one=store.create_conversation(self.a['id'])['id']; two=store.create_conversation(self.a['id'])['id']
  result=run('年假制度',docs=store.documents(self.a)); store.save_turn(self.a['id'],one,'年假制度',result)
  self.assertEqual(store.conversation_history(self.a['id'],one),['年假制度']); self.assertEqual(store.conversation_history(self.a['id'],two),[])
  self.assertEqual(len(store.conversation_turns(self.a,one)),1)
  with self.assertRaises(ValueError): store.conversation_turns(self.b,one)
  with self.assertRaises(ValueError): store.save_turn(self.b['id'],one,'越权',result)
  store.forget(self.a['id']); self.assertEqual(store.conversation_history(self.a['id'],one),[]); self.assertEqual(len(store.conversation_turns(self.a,one)),1)
 def test_revoked_document_redacts_history(self):
  d=store.add_document('财务预算','财务','2026-01-01','预算审批需要主管确认。',access=['财务']); conv=store.create_conversation(self.a['id'])['id']
  result=run('预算审批',docs=store.documents(self.a)); self.assertTrue(result['citations']); store.save_turn(self.a['id'],conv,'预算审批',result)
  self.assertIn('预算审批',store.conversation_turns(self.a,conv)[0]['result']['answer'])
  store.set_doc_access([d['id']],['人事']); r=store.conversation_turns(self.a,conv)[0]['result']; self.assertEqual(r['status'],'access_changed'); self.assertEqual(r['citations'],[]); self.assertEqual(r['trace'],[])

