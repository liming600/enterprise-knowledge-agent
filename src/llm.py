"""OpenAI-compatible chat client with configurable providers."""
import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from http_utils import read_json


def configuration():
    values = {}
    env_file = Path(os.environ.get('KNOWLEDGE_ENV_FILE', Path(__file__).resolve().parents[1] / '.env'))
    if env_file.is_file():
        for line in env_file.read_text(encoding='utf-8-sig').splitlines():
            if '=' in line and not line.lstrip().startswith('#'):
                key, value = line.split('=', 1)
                values[key.strip()] = value.strip().strip('"\'')
    values.update(os.environ)
    return values


def settings():
    values = configuration()
    provider = values.get('LLM_PROVIDER', 'deepseek').lower()
    if provider not in ('deepseek', 'relay', 'openai'):
        raise ValueError('LLM_PROVIDER 必须是 deepseek、relay 或 openai')
    prefix = provider.upper()
    defaults = {'deepseek': ('https://api.deepseek.com', 'deepseek-flash'),
                'relay': ('https://yunwu.ai/v1', 'gpt-5.5'),
                'openai': ('https://api.openai.com/v1', 'gpt-4o-mini')}
    return provider, values.get(prefix + '_API_KEY', ''), values.get(prefix + '_BASE_URL', defaults[provider][0]).rstrip('/'), values.get(prefix + '_MODEL', defaults[provider][1])


def complete(messages, max_tokens=2048, budget=None):
    provider, key, base, model = settings()
    if not key:
        raise ValueError(f'未配置 {provider.upper()}_API_KEY')
    if budget is not None:
        budget.model(messages, max_tokens)
    payload = {'model': model, 'messages': messages, 'temperature': 0.1, 'max_tokens': max_tokens, 'stream': False}
    if provider == 'deepseek':
        payload['thinking'] = {'type': 'disabled'}
    request = urllib.request.Request(base + '/chat/completions', data=json.dumps(payload, ensure_ascii=False).encode('utf-8'), headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=budget.timeout(60) if budget is not None else 60) as response:
            data = read_json(response, 1024*1024, budget)
            if budget is not None: budget.record_usage(data.get('usage'))
            content = data['choices'][0]['message']['content'] or ''
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f'模型接口返回 HTTP {exc.code}') from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError('模型接口无法连接或超时') from exc
    return content.strip()


def decide(messages, budget=None):
    content = complete(messages, max_tokens=1200, budget=budget)
    content = re.sub(r'^```(?:json)?\s*|\s*```$', '', content.strip())
    start, end = content.find('{'), content.rfind('}')
    if start < 0 or end < start:
        raise ValueError('模型未返回有效 JSON')
    result = json.loads(content[start:end + 1])
    if not isinstance(result, dict) or not isinstance(result.get('argument'), str):
        raise ValueError('模型决策格式无效')
    return result
