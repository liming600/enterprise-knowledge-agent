import json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from agent import run

def doc(id,title,text): return {'id':id,'title':title,'date':'2026-01-01','text':text}
cases=[{'name':'明确答案','question':'年假制度','expected':'completed'}, {'name':'文档冲突','question':'报销期限多久','expected':'conflict'}, {'name':'资料外','question':'量子引擎火星殖民','expected':'insufficient'}, {'name':'改写','question':'报账需要几天','expected':'conflict'}, {'name':'上下文追问','question':'那是多少天','history':['年假制度'],'expected':'completed'}, {'name':'工具失败','question':'报销期限多久','fail':True,'expected':'tool_error'}, {'name':'步数上限','question':'报销期限多久','max_steps':1,'expected':'incomplete'}, {'name':'中文数字冲突','question':'试用期多久','docs':[doc('D11','试用期制度','试用期为三个月。'),doc('D12','试用期规定','试用期为六个月。')],'expected':'conflict'}, {'name':'允许与禁止冲突','question':'可以远程办公吗','docs':[doc('D21','远程办公制度','员工可以远程办公。'),doc('D22','远程办公规定','员工不得远程办公。')],'expected':'conflict'}, {'name':'多个互补文档','question':'报销申请流程','docs':[doc('D31','报销流程','报销申请需要填写表格。'),doc('D32','报销规定','报销申请需要经理审批。')],'expected':'needs_clarification'}, {'name':'替换前','question':'报销期限多久','docs':[doc('D01','报销制度','报销期限为30天。')],'expected':'completed'}, {'name':'替换后','question':'报销期限多久','docs':[doc('D01','报销制度','报销期限为45天。')],'expected':'completed'}]
records=[]
for case in cases:
 kwargs={k:v for k,v in case.items() if k not in ('name','question','expected')}
 result=run(case['question'],**kwargs); records.append({'input':case,'output':result,'checks':{'status_matches':result['status']==case['expected']}})
Path(__file__).with_name('runs.json').write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf-8')
print('离线案例',len(records),'通过',sum(x['checks']['status_matches'] for x in records))
