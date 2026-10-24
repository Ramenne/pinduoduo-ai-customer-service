"""区分可选的问题关联和会产生状态副作用的目标引用，不按编号判定反馈真假。"""
import copy


class ReferenceRepairRequired(ValueError):
    pass


def normalize(decision, context):
    value = copy.deepcopy(decision)
    issues = {i['id']: i for i in context.get('issues', [])}
    requests = {r['id'] for r in context.get('requests', [])}
    requests.update(i['request_id'] for i in issues.values() if i.get('request_id'))
    sources = {s['id'] for s in context.get('operator_sources', [])}
    facts = {f['id']: f for f in [*context.get('facts', []), *context.get('historical_facts', [])]}
    references = {f"{r['source']}:{r['id']}" for r in context.get('knowledge_references', [])}
    aliases = [i.get('local_ref') for i in value['issues'] if i.get('local_ref')]
    if len(aliases) != len(set(aliases)) or set(aliases) & set(issues):
        raise ReferenceRepairRequired('local_ref必须是本轮唯一的临时标签，不能冒充已有问题ID')
    targets = [i['id'] for i in value['issues'] if i['id']]
    if len(targets) != len(set(targets)):
        raise ReferenceRepairRequired('同一已有问题只输出一个最终决定，不对同一ID输出冲突动作')
    for issue in value['issues']:
        if issue['id'] and issue['id'] not in issues:
            raise ReferenceRepairRequired('id必须复制上下文已有问题ID；新问题id为空，不得自编编号')
        if issue.get('request_id') and issue['request_id'] not in requests:
            raise ReferenceRepairRequired('request_id必须引用已有待补；新待补request_id为空')
        previous = issues.get(issue['id'])
        if previous and previous['object_ref'] and issue['object_ref'] != previous['object_ref']:
            raise ReferenceRepairRequired('对象变更应新建关联问题，不能覆盖原问题的商品或规格')
    dropped = 0
    for issue in value['issues']:
        valid = []
        for reference in issue.get('related_ids', []):
            if reference in issues or reference in aliases:
                if reference not in valid and reference not in (issue['id'], issue.get('local_ref')):
                    valid.append(reference)
            else:
                dropped += 1
        issue['related_ids'] = valid
    for fact in value['facts']:
        if fact['source_id'] not in sources:
            raise ReferenceRepairRequired('事实source_id只能复制真实人工来源ID，不能引用顾客消息或自编编号')
        if fact.get('supersedes') and fact['supersedes'] not in facts:
            raise ReferenceRepairRequired('supersedes只能指向已有事实；未定位原事实则留空并保留范围')
        if fact.get('corrects_knowledge') and fact['corrects_knowledge'] not in references:
            raise ReferenceRepairRequired('corrects_knowledge只能引用实际提供的知识引用，未定位则留空')
    warnings = list(value.get('reference_warnings', []))
    if dropped:
        warning = {'code': 'unresolved_optional_relation', 'count': dropped}
        if warning not in warnings:
            warnings.append(warning)
    value['reference_warnings'] = warnings
    return value
