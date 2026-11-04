"""Run real model evaluations on synthetic cases; no secrets or private data are stored."""
import json,sys,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from agent import run
ROOT=Path(__file__).resolve().parents[1]
BASE=json.loads((ROOT/'data/docs.json').read_text(encoding='utf-8'))
def doc(id,title,text): return {'id':id,'title':title,'date':'2026-01-01','text':text}
CASES=[
 {'name':'明确答案','question':'年假制度是怎样规定的？','docs':[BASE[2]],'expected':['completed'],'must_include':['5天']},
 {'name':'报销冲突','question':'报销期限多久？','docs':BASE[:2],'expected':['conflict'],'must_include':['30天','15天']},
 {'name':'改写冲突','question':'报账最晚需要几天内提交？','docs':BASE[:2],'expected':['conflict'],'must_include':['30天','15天']},
 {'name':'上下文追问','question':'那是多少天？','history':['请问公司年假制度是什么？'],'docs':[BASE[2]],'expected':['completed'],'must_include':['5天']},
 {'name':'资料不足','question':'公司产假和生育津贴如何规定？','docs':[BASE[2]],'expected':['insufficient','incomplete','needs_clarification'],'must_include':[]},
 {'name':'工具故障','question':'年假有多少天？','docs':[BASE[2]],'fail':True,'expected':['tool_error','incomplete'],'must_include':[]},
 {'name':'替换前','question':'报销期限是多少天？','docs':[doc('D01','报销制度','报销期限为30天。')],'expected':['completed'],'must_include':['30天']},
 {'name':'替换后','question':'报销期限是多少天？','docs':[doc('D01','报销制度','报销期限为45天。')],'expected':['completed'],'must_include':['45天']},
 {'name':'其他数值冲突','question':'试用期有多长？','docs':[doc('D11','试用期制度','试用期为三个月。'),doc('D12','试用期规定','试用期为六个月。')],'expected':['conflict'],'must_include':['三个月','六个月']},
 {'name':'允许与禁止冲突','question':'是否允许远程办公？','docs':[doc('D21','远程办公制度','员工可以远程办公。'),doc('D22','远程办公规定','员工不得远程办公。')],'expected':['conflict'],'must_include':['可以远程办公','不得远程办公']},
 {'name':'单文档语义冲突','question':'年假安排由谁决定？','docs':[doc('D31','年假安排','年假安排由部门统一决定。年假安排由员工自行决定。')],'expected':['conflict','needs_clarification'],'must_include':['部门统一决定','员工自行决定']},
]

def assess(case,r):
 status=r.get('status'); answer=r.get('answer',''); citations=r.get('citations',[])
 docs={d['id']:d for d in case['docs']}
 source_valid=all(c['id'] in docs and c.get('snippet','') and c['snippet'] in docs[c['id']]['text'] for c in citations)
 metadata_valid=all(c['id'] in docs and c.get('date')==docs[c['id']].get('date') and c.get('version')==docs[c['id']].get('version') for c in citations)
 needs_sources=status in ('completed','conflict','needs_clarification')
 return {'status_matches':status in case['expected'],'required_terms_present':all(x in answer for x in case['must_include']),'citations_match_source':source_valid and (bool(citations) if needs_sources else True),'citation_metadata_matches':metadata_valid,'answer_contains_cited_text':all(c.get('snippet','') in answer for c in citations) if needs_sources else True,'has_trace':bool(r.get('trace'))}

if __name__=='__main__':
 out=[]; path=ROOT/'examples/real-runs.json'
 for case in CASES:
  args={k:v for k,v in case.items() if k in ('docs','history','fail')}
  start=time.monotonic(); result=run(case['question'],mode='real',**args)
  row={'input':case,'output':result,'checks':assess(case,result),'elapsed_seconds':round(time.monotonic()-start,2)}
  out.append(row);path.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
  print(case['name'],result.get('status'),'检查:',row['checks'],flush=True)
