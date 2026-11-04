import sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,'src')
import store,memory,retrieval
from agent import run

class HybridTests(unittest.TestCase):
 def test_dense_and_bm25_fusion(self):
  docs=[{'id':'D1','title':'售后政策','date':'2026-01-01','text':'商品支持三十日退款。'}, {'id':'D2','title':'出勤制度','date':'2026-01-01','text':'员工按时签到。'}]
  hits,meta=retrieval.search('客户如何退货',docs,'real',embedding_fn=lambda texts:[[1,0],[1,0],[0,1]])
  self.assertEqual(meta['vector'],'dense_embedding'); self.assertEqual(hits[0]['id'],'D1'); self.assertEqual(hits[0]['snippet'],'商品支持三十日退款。')
 def test_fallback_and_authorized_scope(self):
  docs=[{'id':'D1','title':'报销制度','date':'2026-01-01','text':'报销期限30天。'}]
  def fail(_): raise RuntimeError('offline')
  hits,meta=retrieval.search('报销期限',docs,'real',embedding_fn=fail); self.assertEqual(meta['vector'],'local_char_vector'); self.assertIn('降级',meta['reason']); self.assertEqual([x['id'] for x in hits],['D1'])
 def test_overflow_is_not_missing_evidence(self):
  docs=[{'id':'D1','title':'报销制度','date':'2026-01-01','text':'报销规定。'*22000}]
  result=run('报销期限',docs=docs); self.assertEqual(result['status'],'incomplete'); self.assertTrue(result['retrieval']['limit_exceeded'])
 def test_long_document_chunk_citation_exact(self):
  doc={'id':'D1','title':'产品制度','date':'2026-01-01','text':'其他条款。'*300+'报销期限45天。'+'其他规定。'*300}
  result=run('报销期限',docs=[doc]); self.assertEqual(result['status'],'completed'); c=result['citations'][0]; self.assertIn('报销期限45天',c['snippet']); self.assertIn(c['snippet'],doc['text']); self.assertIn('chunk_id',c)
 def test_prompt_budget_is_enforced(self):
  r=run('年假',history=['测'*3000]*6,mode='real',decision_fn=lambda _:self.fail('must stop before model'))
  self.assertEqual(r['status'],'incomplete')
 def test_summary_supplies_topic_to_followup(self):
  r=run('那是多少天',history=['谢谢'],memory_summary='此前用户咨询年假制度。')
  self.assertEqual(r['status'],'completed'); self.assertIn('D03',r['answer'])

class MemoryTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(); self.old=store.DB_PATH; store.DB_PATH=Path(self.tmp.name)/'m.db'; store.init(); self.u=store.identity(store.login('admin','admin123')); self.c=store.create_conversation(self.u['id'])['id']
 def tearDown(self): store.DB_PATH=self.old; self.tmp.cleanup()
 def add(self,n):
  for i in range(n): store.save_turn(self.u['id'],self.c,'第%d个问题：了解报销流程'%i,{'status':'completed','answer':'答案','citations':[]})
 def test_summary_generated_cached_and_deleted(self):
  self.add(9); calls=[]
  def summarizer(messages,max_tokens): calls.append(messages); return '用户此前询问报销流程。'
  first=memory.build(self.u['id'],self.c,'real',summarizer); self.assertEqual(first['source_count'],3); self.assertEqual(first['method'],'model'); self.assertEqual(len(first['history']),6)
  second=memory.build(self.u['id'],self.c,'real',summarizer); self.assertEqual(len(calls),1); self.assertEqual(first['summary'],second['summary'])
  item=store.memories(self.u['id'])[-1]; store.forget(self.u['id'],item['id']); self.assertIsNone(store.get_summary(self.u['id'],self.c))
  self.assertEqual(memory.build(self.u['id'],self.c,'mock')['method'],'extractive')
 def test_failure_fallback_and_concurrent_deletion(self):
  self.add(8)
  def fail(messages,max_tokens): raise RuntimeError('offline')
  r=memory.build(self.u['id'],self.c,'real',fail); self.assertEqual(r['method'],'extractive_fallback'); self.assertTrue(r['summary'])
  store.clear_summary(self.u['id'],self.c)
  def delete(messages,max_tokens): store.forget(self.u['id']); return '旧摘要'
  r=memory.build(self.u['id'],self.c,'real',delete); self.assertEqual(r['method'],'stale'); self.assertEqual(r['summary'],''); self.assertIsNone(store.get_summary(self.u['id'],self.c))
 def test_summary_is_user_scoped(self):
  self.add(8); memory.build(self.u['id'],self.c,'mock'); store.add_departments(['财务']);store.bulk_create_users([{'username':'a','password':'password1','department':'财务'}]); other=store.identity(store.login('a','password1'))
  self.assertEqual(store.summaries(other['id']),[])
  with self.assertRaises(ValueError): memory.build(other['id'],self.c,'mock')
 def test_delete_only_invalidates_own_conversation_summary(self):
  self.add(8); memory.build(self.u['id'],self.c,'mock'); other=store.create_conversation(self.u['id'])['id']
  for i in range(8): store.save_turn(self.u['id'],other,'其他问题%d'%i,{'status':'completed','answer':'答案','citations':[]})
  memory.build(self.u['id'],other,'mock'); first_id=store.memory_items(self.u['id'],self.c)[0]['id']; store.forget(self.u['id'],first_id)
  self.assertIsNone(store.get_summary(self.u['id'],self.c)); self.assertIsNotNone(store.get_summary(self.u['id'],other))
