"""内部协作和对客草稿的输出边界。解析失败不得把整段内部说明当回复。"""
import json
from agent.customer_voice import contains_internal_diagnostics


def decode(text):
    text = (text or '').strip()
    if text.startswith('```'):
        text = text.split('\n', 1)[-1].rsplit('```', 1)[0].strip()
    if len(text) > 20000:
        raise ValueError('AI输出过长，请重试')
    try:
        value = json.loads(text)
    except (TypeError, ValueError):
        raise ValueError('AI未按协作格式返回，请重试；未生成可发送草稿') from None
    if not isinstance(value, dict):
        raise ValueError('AI协作结果格式无效，请重试')
    return value


def string(value, key, max_length, optional=False):
    text = value.get(key, '' if optional else None)
    if not isinstance(text, str) or (not optional and not text.strip()) or len(text) > max_length:
        raise ValueError('AI协作结果内容无效，请重试')
    return text.strip()


def parse_draft(text):
    value = decode(text)
    reply = string(value, 'customer_reply', 6000)
    if contains_internal_diagnostics(reply):
        raise ValueError('对客草稿包含内部处理说明，未生成可发送草稿，请重新生成')
    return reply, string(value, 'operator_note', 4000, optional=True)


def parse_advice(text):
    value = decode(text)
    message = string(value, 'message', 6000)
    requests, resolved = value.get('requests'), value.get('resolved_request_ids')
    withdrawn = value.get('withdrawn_request_ids',[])
    if (not isinstance(requests, list) or len(requests) > 8 or not isinstance(resolved, list)
        or len(resolved) > 32 or any(not isinstance(x, str) or len(x) > 36 for x in resolved)
        or not isinstance(withdrawn,list) or len(withdrawn)>32
        or any(not isinstance(x,str) or len(x)>36 for x in withdrawn)):
        raise ValueError('AI待补事项格式无效，请重试')
    for r in requests:
        if not isinstance(r, dict) or r.get('id') is not None and (not isinstance(r['id'], str) or len(r['id']) > 36):
            raise ValueError('AI待补事项格式无效，请重试')
        string(r, 'question', 500)
    return message, requests, resolved, withdrawn
