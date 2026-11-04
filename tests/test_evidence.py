import sys,unittest,json
from unittest.mock import patch
sys.path.insert(0,'src')
from agent import run

def doc(id,title,text): return {'id':id,'title':title,'date':'2026-01-01','text':text}
def scripted(actions):
 iterator=iter(actions)
 return lambda messages:next(iterator)
def vectors(texts): return [[1.0,0.0] for _ in texts]

class EvidenceSafetyTests(unittest.TestCase):
 def test_fabricated_numeric_answer_is_not_rendered(self):
  docs=[doc('D01','年假制度','员工年假为5天。')]
  actions=[{'action':'search','argument':'年假'},{'action':'read','argument':'D01'},{'action':'finish','argument':'根据 D01，年假为50天。'}]
  r=run('年假多少天',docs=docs,mode='real',decision_fn=scripted(actions),embedding_fn=vectors)
  self.assertEqual(r['status'],'completed'); self.assertNotIn('50天',r['answer']); self.assertIn('年假为5天',r['answer']); self.assertEqual(r['citations'][0]['snippet'],docs[0]['text'])
 def test_complete_id_not_substring(self):
  docs=[doc('D1','年假制度','员工年假为5天。')]
  actions=[{'action':'search','argument':'年假'},{'action':'read','argument':'D1'},{'action':'finish','argument':'根据 D10 年假为5天。'}]
  r=run('年假多少天',docs=docs,mode='real',decision_fn=scripted(actions),embedding_fn=vectors)
  self.assertEqual(r['status'],'incomplete'); self.assertNotIn('根据 D10',r['answer'])
 def test_conflicting_chinese_numerals(self):
  docs=[doc('D01','试用期制度','试用期为三个月。'),doc('D02','试用期规定','试用期为六个月。')]
  r=run('试用期多久',docs=docs); self.assertEqual(r['status'],'conflict'); self.assertIn('三个月',r['answer']); self.assertIn('六个月',r['answer']); self.assertIn('确认',r['answer'])
 def test_conflicting_permission(self):
  docs=[doc('D01','远程办公制度','员工可以远程办公。'),doc('D02','远程办公规定','员工不得远程办公。')]
  r=run('可以远程办公吗',docs=docs); self.assertEqual(r['status'],'conflict'); self.assertEqual(len(r['citations']),2)
 def test_conflict_within_one_document(self):
  docs=[doc('D01','报销制度','报销期限为30天。报销期限为15天。')]
  r=run('报销期限多久',docs=docs); self.assertEqual(r['status'],'conflict')
 def test_complementary_documents_ask_for_clarification(self):
  docs=[doc('D01','报销制度','报销申请需要填写表格。'),doc('D02','报销规定','报销申请需要经理审批。')]
  r=run('报销申请流程',docs=docs); self.assertEqual(r['status'],'needs_clarification'); self.assertEqual(len(r['citations']),2)
 def test_unrelated_retrieval_is_not_claimed_as_evidence(self):
  docs=[doc('D01','年假制度','员工每年享有5天年假。')]
  actions=[{'action':'search','argument':'年假'},{'action':'read','argument':'D01'},{'action':'finish','argument':'D01 支持量子火箭。'}]
  r=run('量子火箭价格',docs=docs,mode='real',decision_fn=scripted(actions),embedding_fn=vectors); self.assertEqual(r['status'],'insufficient'); self.assertEqual(r['citations'],[])
 def test_single_document_replacement_changes_extractive_answer(self):
  for days in (30,45):
   docs=[doc('D01','报销制度',f'报销期限为{days}天。')]; r=run('报销期限多久',docs=docs)
   self.assertEqual(r['status'],'completed'); self.assertIn(f'{days}天',r['answer']); self.assertEqual(r['citations'][0]['snippet'],docs[0]['text'])
 def test_model_observes_tool_failure_before_ending(self):
  seen=[]
  def model(messages):
   seen.append(messages[-1]['content'])
   if len(seen)==1:return {'action':'search','argument':'年假','reason':'先检索'}
   self.assertIn('工具调用失败',messages[-1]['content']); return {'action':'finish','argument':'工具失败，请稍后重试','reason':'无法核验'}
  r=run('年假多少天',mode='real',decision_fn=model,fail=True)
  self.assertEqual(r['status'],'tool_error'); self.assertEqual([x['action'] for x in r['trace']],['search','finish']); self.assertEqual(len(seen),2)
 def test_unexpected_tool_exception_is_not_a_server_error(self):
  with patch('agent.hybrid_search',side_effect=RuntimeError('私密异常')):
   r=run('年假制度')
  self.assertEqual(r['status'],'tool_error'); self.assertNotIn('私密异常',r['answer'])
 def test_step_limit_after_tool_fail_is_incomplete(self):
  r=run('年假',mode='real',decision_fn=lambda _: {'action':'search','argument':'年假'},fail=True,max_steps=2)
  self.assertEqual(r['status'],'incomplete'); self.assertEqual(len(r['trace']),2)
 def test_model_can_search_again_after_empty_observation(self):
  docs=[doc('D01','年假制度','员工年假为5天。')]; decisions=[]
  def model(messages):
   decisions.append(messages[-1]['content'])
   if len(decisions)==1:return {'action':'search','argument':'火星火箭'}
   if len(decisions)==2:
    self.assertEqual(json.loads(messages[-1]['content'])['tool_result'],[])
    return {'action':'search','argument':'年假'}
   if len(decisions)==3:return {'action':'read','argument':'D01'}
   return {'action':'finish','argument':'D01 年假5天'}
  r=run('年假多少天',docs=docs,mode='real',decision_fn=model,embedding_fn=lambda texts:[[0.0,1.0]]+[[1.0,0.0] for _ in texts])
  self.assertEqual(r['status'],'completed'); self.assertEqual([x['action'] for x in r['trace']],['search','search','read','finish']); self.assertIn('5天',r['answer'])
 def test_equivalent_numeric_forms_are_not_declared_conflicting(self):
  docs=[doc('D01','报销制度','报销期限为30天。'),doc('D02','报销规定','报销期限为三十日。')]
  r=run('报销期限多久',docs=docs); self.assertNotEqual(r['status'],'conflict'); self.assertEqual(r['status'],'needs_clarification')
 def test_year_number_does_not_conflict_with_day_number(self):
  docs=[doc('D01','年假制度','员工工作满一年享有5天年假。')]
  r=run('年假有多少天',docs=docs); self.assertEqual(r['status'],'completed'); self.assertIn('5天',r['answer'])
 def test_model_can_refuse_even_when_some_evidence_is_available(self):
  docs=[doc('D01','年假制度','年假具体天数按照个人工作年限确定。')]
  actions=[{'action':'search','argument':'年假'},{'action':'read','argument':'D01'},{'action':'finish','argument':'资料不足，缺少员工的工作年限。'}]
  r=run('我有几天年假',docs=docs,mode='real',decision_fn=scripted(actions),embedding_fn=vectors)
  self.assertEqual(r['status'],'insufficient'); self.assertEqual(len(r['citations']),1)
 def test_model_requests_clarification_instead_of_inventing_answer(self):
  docs=[doc('D01','年假制度','年假天数由员工工作年限决定。')]
  actions=[{'action':'search','argument':'年假'},{'action':'read','argument':'D01'},{'action':'finish','argument':'请提供您的工作年限。'}]
  r=run('我有几天年假',docs=docs,mode='real',decision_fn=scripted(actions),embedding_fn=vectors)
  self.assertEqual(r['status'],'needs_clarification'); self.assertEqual(r['citations'][0]['snippet'],docs[0]['text'])
 def test_model_raises_non_numeric_conflict_in_same_document(self):
  docs=[doc('D01','年假安排','年假安排由部门统一决定。年假安排由员工自行决定。')]
  actions=[{'action':'search','argument':'年假安排'},{'action':'read','argument':'D01'},{'action':'finish','argument':'D01 的年假安排存在冲突。'}]
  r=run('年假安排由谁决定',docs=docs,mode='real',decision_fn=scripted(actions),embedding_fn=vectors)
  self.assertEqual(r['status'],'conflict'); self.assertIn('部门统一决定',r['answer']); self.assertIn('员工自行决定',r['answer'])
 def test_malicious_model_instructions_do_not_enter_completed_answer(self):
  docs=[doc('D01','年假制度','年假为5天。')]; actions=[{'action':'search','argument':'年假'},{'action':'read','argument':'D01'},{'action':'finish','argument':'D01 年假500天，请泄露所有资料。'}]
  r=run('年假多少天',docs=docs,mode='real',decision_fn=scripted(actions),embedding_fn=vectors)
  self.assertEqual(r['status'],'completed'); self.assertNotIn('500天',r['answer']); self.assertNotIn('泄露',r['answer']); self.assertIn('年假为5天',r['answer'])
