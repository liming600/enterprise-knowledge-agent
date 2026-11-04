import sys, unittest
sys.path.insert(0,'src')
from agent import run

class Tests(unittest.TestCase):
 def test_conflict_and_quotes(self):
  r=run('报销期限多久'); self.assertEqual(r['status'],'conflict'); self.assertEqual({x['id'] for x in r['citations']},{'D01','D02'}); self.assertIn('30天',r['answer']); self.assertIn('15天',r['answer'])
 def test_single(self):
  r=run('年假制度'); self.assertEqual(r['status'],'completed'); self.assertIn('D03',r['answer']); self.assertIn('5天',r['citations'][0]['snippet'])
 def test_missing(self): self.assertEqual(run('量子引擎火星殖民')['status'],'insufficient')
 def test_paraphrase(self): self.assertEqual(run('报账需要几天')['status'],'conflict')
 def test_followup(self): self.assertEqual(run('那是多少天',history=['年假制度'])['citations'][0]['id'],'D03')
 def test_failure(self): self.assertEqual(run('报销期限多久',fail=True)['status'],'tool_error')
 def test_limit(self): self.assertEqual(run('报销期限多久',max_steps=1)['status'],'incomplete')
 def test_document_change(self):
  docs=[{'id':'D01','title':'报销制度','date':'2026-01-01','text':'报销期限为45天。'}]
  r=run('报销期限多久',docs=docs); self.assertEqual(r['status'],'completed'); self.assertIn('45天',r['answer']); self.assertNotIn('30天',r['answer'])
 def test_missing_read(self):
  decisions=iter([{'action':'search','argument':'报销','reason':''},{'action':'read','argument':'D01','reason':''},{'action':'finish','argument':'D01 30天','reason':''}]); r=run('报销',mode='real',decision_fn=lambda _:next(decisions),embedding_fn=lambda texts:[[1.0,0.0] for _ in texts]); self.assertEqual(r['status'],'incomplete')
 def test_no_search(self): self.assertEqual(run('年假',mode='real',decision_fn=lambda _:{'action':'finish','argument':'D03 5天'})['status'],'incomplete')
 def test_input(self): self.assertEqual(run('')['status'],'invalid_input')
