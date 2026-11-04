"""Conservative prompt-injection screening and per-request execution budgets.

Screening is a defense-in-depth heuristic, not a proof that input is safe.
"""
import json
import re
import time

MAX_STEPS = 8
MAX_TOOLS = 6
MAX_MODELS = 8
MAX_EMBEDDING_REQUESTS = 8
MAX_TOTAL_INPUT_BYTES = 120000
MAX_SINGLE_INPUT_BYTES = 48000
MAX_SECONDS = 120
MAX_SAME_ACTION = 2

class BudgetExceeded(Exception):
    pass

class Budget:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.started = clock()
        self.model_calls = 0
        self.tool_calls = 0
        self.embedding_requests = 0
        self.model_input_bytes = 0
        self.embedding_input_bytes = 0
        self.output_token_limit = 0
        self.provider_usage = {'prompt_tokens':0, 'completion_tokens':0, 'total_tokens':0}
        self.usage_available = False
        self.provider_responses = 0
        self.responses_with_usage = 0

    def check_time(self):
        if self.clock() - self.started >= MAX_SECONDS:
            raise BudgetExceeded('达到单次任务时间上限')

    def timeout(self, preferred):
        self.check_time()
        return max(0.1, min(preferred, MAX_SECONDS - (self.clock() - self.started)))

    def model(self, messages, output_tokens):
        self.check_time()
        size = len(json.dumps(messages, ensure_ascii=False).encode('utf-8'))
        if self.model_calls >= MAX_MODELS: raise BudgetExceeded('达到模型调用次数上限')
        if size > MAX_SINGLE_INPUT_BYTES: raise BudgetExceeded('单次模型输入超过安全预算')
        if self.model_input_bytes + self.embedding_input_bytes + size > MAX_TOTAL_INPUT_BYTES: raise BudgetExceeded('达到累计输入预算上限')
        self.model_calls += 1
        self.model_input_bytes += size
        self.output_token_limit += output_tokens

    def tool(self):
        self.check_time()
        if self.tool_calls >= MAX_TOOLS: raise BudgetExceeded('达到工具调用次数上限')
        self.tool_calls += 1

    def embedding(self, texts):
        self.check_time()
        size = len(json.dumps(texts, ensure_ascii=False).encode('utf-8'))
        if self.embedding_requests >= MAX_EMBEDDING_REQUESTS: raise BudgetExceeded('达到向量接口请求次数上限')
        if self.model_input_bytes + self.embedding_input_bytes + size > MAX_TOTAL_INPUT_BYTES: raise BudgetExceeded('达到累计输入预算上限')
        self.embedding_requests += 1
        self.embedding_input_bytes += size

    def record_usage(self, usage):
        self.provider_responses += 1
        if not isinstance(usage, dict): return
        seen = False
        for name in self.provider_usage:
            value = usage.get(name)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                self.provider_usage[name] += value
                self.usage_available = True
                seen = True
        if seen: self.responses_with_usage += 1

    def report(self):
        return {'model_calls': self.model_calls, 'model_call_limit': MAX_MODELS,
                'tool_calls': self.tool_calls, 'tool_call_limit': MAX_TOOLS,
                'embedding_requests': self.embedding_requests, 'embedding_request_limit': MAX_EMBEDDING_REQUESTS,
                'model_input_bytes': self.model_input_bytes, 'embedding_input_bytes': self.embedding_input_bytes,
                'input_byte_limit': MAX_TOTAL_INPUT_BYTES, 'requested_output_token_limit': self.output_token_limit,
                'provider_usage': dict(self.provider_usage) if self.usage_available else None,
                'responses_with_usage': self.responses_with_usage, 'provider_responses': self.provider_responses,
                'elapsed_seconds': round(self.clock()-self.started, 3), 'time_limit_seconds': MAX_SECONDS}

_PATTERNS = [
    re.compile(r'(?:忽略|无视|忘记|覆盖|绕过|废除|跳过).{0,20}(?:之前|先前|以上|上述|原有|系统|开发者|安全|所有).{0,14}(?:指令|提示词|提示|规则|限制|约束|政策)', re.I),
    re.compile(r'(?:ignore|disregard|forget|override|bypass).{0,45}(?:previous|prior|above|system|developer|safety).{0,35}(?:instructions?|prompts?|rules?|polic(?:y|ies)|messages?)', re.I|re.S),
    re.compile(r'(?:输出|打印|泄露|发送|展示|公开|复制|导出|传输).{0,24}(?:系统提示词|隐藏提示词|系统提示|开发者指令|api[ _-]?key|密钥|密码|令牌|token)', re.I),
    re.compile(r'(?:reveal|print|exfiltrate|send|disclose|leak).{0,45}(?:system prompt|developer instructions|api[ _-]?key|password|secret|access token)', re.I|re.S),
    re.compile(r'(?:忽略|无视|跳过).{0,12}(?:其他|其它|其余|剩余).{0,12}(?:文档|资料|来源|规定|规则)', re.I),
    re.compile(r'(?:从现在开始|即刻|从此).{0,12}(?:扮演|充当|成为).{0,8}(?:系统|开发者)', re.I),
    re.compile(r'(?:you are now|act as|pretend to be).{0,25}(?:the system|a system|the developer|a developer)', re.I|re.S),
    re.compile(r'(?:发送到|上传到|传输到|上报到).{0,30}https?://', re.I),
    re.compile(r'(?:<\|(?:system|developer|assistant)\|>|\[/?(?:system|developer)\]|<\/?(?:system|developer)>)', re.I),
    re.compile(r'(?:^|\n)\s*(?:system|developer|系统指令|开发者指令)\s*[:：]', re.I),
]
_PROTECTIVE_PREFIX = re.compile(r'(?:(?:不要|禁止|不得|防止|避免|防范|防御|禁止尝试|如何防止|如何避免)\s*|(?:防止|避免|防范|抵御).{0,12}(?:注入|窃取|泄露)[和与及、]?)$')

def suspicious(text):
    if not isinstance(text, str): return False
    normalized = re.sub(r'[\u200b-\u200f\u202a-\u202e\ufeff]', '', text)
    for pattern in _PATTERNS:
        for match in pattern.finditer(normalized):
            if not _PROTECTIVE_PREFIX.search(normalized[max(0, match.start()-24):match.start()]):
                return True
    return False

def safe_action(value):
    if not isinstance(value, dict): return False
    if value.get('action') not in ('search','read','finish'): return False
    if not isinstance(value.get('argument'), str) or not isinstance(value.get('reason', ''), str): return False
    if len(value['argument']) > (6000 if value['action']=='finish' else 300): return False
    if len(value.get('reason', '')) > 1000: return False
    return True
