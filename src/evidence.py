"""Conservative evidence handling: never render unverified model claims as facts."""
import re
from retrieval import tokens

# Normalization is intentionally limited; uncertain scope is reported, not resolved.
NUMBER = r'(?:\d+(?:\.\d+)?|[零〇一二两三四五六七八九十百千]+)'
MEASURE = re.compile(r'('+NUMBER+r')\s*(个?月|天|日|周|年|小时|分钟|元|%|％|次|件|人)')
SPLIT = re.compile(r'(?<=[。！？；;\n])')
FILLER = {'多少','什么','怎么','如何','多久','请问','是否','可以','需要','进行','有关','请告','告诉','情况','政策','制度','规定','相关','期限','适用','员工','公司','哪些','然后','还有','问题','这个','那个','时间','多少','那么','那是','这些','的是','帮助','帮我'}
QUESTION_WORDS = re.compile(r'(多少|几天|多久|多长|什么|哪[些个]?|如何|怎么|可以吗|是否|吗|呢|啊|请问|请告知|是什么|怎么样|有多少|需要|要求|规定|制度|期限|时候|如何|哪些|规则|请|问|的|是|为|有|吗|呢|？|\?)')


def number_value(raw):
    if re.fullmatch(r'\d+(?:\.\d+)?',raw): return float(raw)
    digits={'零':0,'〇':0,'一':1,'二':2,'两':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9}
    if all(x in digits for x in raw): return float(''.join(str(digits[x]) for x in raw))
    total=0; value=0
    for ch in raw:
        if ch in digits: value=digits[ch]
        elif ch in '十百千': total+=(value or 1)*{'十':10,'百':100,'千':1000}[ch]; value=0
        else: return None
    return float(total+value)


def keywords(question, history=None, summary=''):
    # Incorporate recent topic only for a short follow-up with no clear topic.
    text=question
    core=QUESTION_WORDS.sub('',text)
    is_followup=bool(re.match(r'^(那|这|它|其|还有|另外|那么)',question.strip())) or len(core)<2
    if is_followup and history: text=' '.join(history[-2:])+' '+text
    if is_followup and summary: text=summary+' '+text
    return {x for x in tokens(text) if x not in FILLER and not x.isdigit()}


def relevant_evidence(question, evidence, history=None, summary=''):
    keys=keywords(question,history,summary)
    if not keys: return []
    selected=[]
    for entry in evidence:
        title=entry.get('title',''); snippet=entry.get('snippet','')
        content=set(tokens(title+' '+snippet))
        if keys & content: selected.append(entry)
    return selected


def relevant_clauses(question, evidence, history=None, summary=''):
    keys=keywords(question,history,summary)
    out=[]
    for entry in evidence:
        clauses=[x.strip() for x in SPLIT.split(entry['snippet']) if x.strip()]
        matched=[x for x in clauses if keys & set(tokens(x))]
        # Titles often specify the subject omitted in a short single-clause body.
        if not matched and len(clauses)==1 and keys & set(tokens(entry.get('title',''))): matched=clauses
        for clause in matched: out.append((entry,clause))
    return out


def potential_conflict(question, evidence, history=None, summary=''):
    clauses=relevant_clauses(question,evidence,history,summary)
    groups={}
    for entry,clause in clauses:
        for match in MEASURE.finditer(clause):
            n=number_value(match.group(1)); unit=match.group(2)
            if n is None: continue
            unit={'日':'天','个月':'月','％':'%'}.get(unit,unit)
            groups.setdefault(unit,[]).append((n,entry,clause))
    for values in groups.values():
        if len({x[0] for x in values})>1:
            return True
    positives=[]; negatives=[]
    for _,clause in clauses:
        if re.search(r'不允许|不可以|不得|禁止|不支持|不可|不能|不予',clause): negatives.append(clause)
        elif re.search(r'允许|可以|支持|可申请|可办理|可享受',clause): positives.append(clause)
    return bool(positives and negatives)


def exact_document_ids(text, available):
    # IDs must be complete tokens, so D1 is never accepted as a match inside D10.
    found=set(re.findall(r'(?<![A-Za-z0-9_])D[0-9]+(?![A-Za-z0-9_])',text,flags=re.I))
    return {x for x in available if x.upper() in {y.upper() for y in found}}


def render_sources(evidence, intro):
    lines=[intro]
    for c in evidence:
        label=c['id']+(' · v'+str(c['version']) if c.get('version') is not None else '')+' · '+str(c.get('date',''))
        lines.append(f"【{label}】{c['snippet']}")
    return '\n'.join(lines)
