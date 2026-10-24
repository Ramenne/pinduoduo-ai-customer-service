"""跨入口业务决定的有界Contract。只解释业务，不授予执行权限。"""
import json

VERSION = 'oin-business-1'
ACTIONS = {'reply', 'ask_customer', 'request_staff', 'handoff', 'resolve', 'no_reply'}


def text(value, key, limit, required=False):
    result = value.get(key, '')
    if result is None and not required:
        result = ''
    if not isinstance(result, str) or len(result) > limit or (required and not result.strip()):
        raise ValueError('业务决定字段无效: ' + key)
    return result.strip()


def parse(raw):
    if not isinstance(raw, str) or len(raw) > 30000:
        raise ValueError('业务决定过长或无效')
    if raw.strip().startswith('```'):
        raw = raw.strip().split('\n', 1)[-1].rsplit('```', 1)[0]
    value = json.loads(raw)
    if not isinstance(value, dict) or value.get('version') != VERSION:
        raise ValueError('业务决定版本无效')
    result = {'version': VERSION, 'message': text(value, 'message', 4000),
        'customer_reply': text(value, 'customer_reply', 6000), 'issues': [], 'facts': []}
    for key, limit in [('issues', 12), ('facts', 12)]:
        rows = value.get(key)
        if not isinstance(rows, list) or len(rows) > limit or any(not isinstance(r, dict) for r in rows):
            raise ValueError('业务决定列表无效: ' + key)
    for item in value['issues']:
        action = item.get('action')
        if action not in ACTIONS:
            raise ValueError('业务动作无效')
        related = item.get('related_ids', [])
        if not isinstance(related, list) or len(related) > 8 or any(not isinstance(x,str) or len(x)>36 for x in related):
            raise ValueError('关联问题格式无效')
        result['issues'].append({'local_ref':text(item, 'local_ref', 36), 'related_ids':related, 'id': text(item, 'id', 36), 'topic': text(item, 'topic', 200, True),
            'object_ref': text(item, 'object_ref', 200), 'action': action,
            'question': text(item, 'question', 500), 'reason': text(item, 'reason', 500),
            'request_id': text(item, 'request_id', 36)})
        if action in ('request_staff', 'ask_customer', 'handoff') and not result['issues'][-1]['question']:
            raise ValueError('必要缺口未说明')
    for item in value['facts']:
        if item.get('lifetime') not in ('temporary', 'session', 'reusable'):
            raise ValueError('事实有效范围无效')
        if item.get('lifetime') == 'reusable' and item.get('category') not in ('product', 'service'):
            raise ValueError('长期事实须指定两类知识库之一')
        result['facts'].append({'category': text(item, 'category', 16),'source_id': text(item, 'source_id', 36, True),
            'text': text(item, 'text', 1500, True), 'object_ref': text(item, 'object_ref', 200),
            'scope': text(item, 'scope', 300, True), 'lifetime': item['lifetime'],
            'supersedes': text(item, 'supersedes', 36),
            'corrects_knowledge': text(item, 'corrects_knowledge', 100)})
    result['observations'] = []
    observations = value.get('observations',[])
    if not isinstance(observations,list) or len(observations)>4 or any(not isinstance(o,dict) for o in observations):
        raise ValueError('运行经验列表无效')
    for o in observations:
        if o.get('category') not in ('interpretation','retrieval','execution'):
            raise ValueError('运行经验分类无效')
        result['observations'].append({k:text(o,k,n,True) for k,n in [('source_id',36),('category',32),('title',200),('observation',2000),('guidance',2000),('trigger_terms',400)]})
    if any(i['action'] in ('reply', 'ask_customer') for i in result['issues']) and not result['customer_reply']:
        raise ValueError('对客动作缺少正文')
    if result['customer_reply'] and not any(i['action'] in ('reply', 'ask_customer') for i in result['issues']):
        raise ValueError('对客正文缺少对应动作')
    if not result['issues'] and not result['facts'] and not result['message']:
        raise ValueError('业务决定为空')
    return result
