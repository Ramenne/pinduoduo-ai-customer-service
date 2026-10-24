"""复用会话控制行与原知识任务：逐问题、带来源事实及业务决定持久化。"""
import copy
import json
import uuid
from datetime import datetime
from common.utils.time_utils import now_beijing_naive
from sqlalchemy import select
from common.models.shop_models import Shop
from common.models.knowledge_curation_models import KnowledgeCuration
from common.services import ai_workspace, human_workspace, conversation_control as control
from common.services.harness_contract import VERSION


def read(row):
    value = json.loads(row.harness_json) if row and row.harness_json else None
    value = value or {'version': VERSION, 'issues': [], 'facts': [], 'rounds': []}
    for issue in value['issues']:
        # 旧记录仅按实际还保存的轮次补来源；没有证据时不编造最初消息。
        known = [turn.get('source_message_id') for turn in value['rounds']
            if issue['id'] in turn.get('issues', [])]
        known += [issue.get('source_message_id')]
        known = [i for i in known if isinstance(i, int) and i > 0]
        if known:
            issue.setdefault('origin_message_id', known[0])
            issue.setdefault('source_message_ids', list(dict.fromkeys(known)))
    return value


def context(session, pk, uid, *, kind, source_id=None, query=""):
    from common.services import context_archive, harness_experience
    row = ai_workspace.row_for(session, pk, uid)
    reconcile(session, row)
    state = read(row)
    if row:
        context_archive.preserve_rounds(session,row,state)
        state = context_archive.compact(session, row, state)
        row.harness_json = json.dumps(state, ensure_ascii=False)
    workspace = human_workspace.migrate_archives(session,row) or {}
    entries = workspace.get('entries', []) if not workspace.get('closed') else []
    sources = [{'id': e['id'], 'text': e['content'], 'kind': e['kind'], 'actor_id': e.get('actor_id'), 'at':e.get('at')} for e in entries
        if e['kind'] in ('fact', 'instruction')]
    inbound = ai_workspace.latest_inbound(session, pk, uid)
    current_source = next((e for e in entries if e['id']==source_id), {})
    inbound_row = ai_workspace.latest_message_row(session,pk,uid,inbound=True)
    query = query or current_source.get('content') or (ai_workspace.message_content(inbound_row) if inbound_row else '')
    recalled, navigation, trace = context_archive.recall(session,pk,uid,query,state,answer_id=current_source.get('answer_id'))
    combined = context_archive.restore(state, {'recalled':recalled})
    # 旧待补不凭空消失，模型可明确沿用或撤回。
    result = {'event': {'kind': kind, 'source_id': source_id},
        'revision': row.revision if row else 0,
        'source_message_id': inbound,
        'boundary': ai_workspace.latest_message(session, pk, uid),
        'issues': combined['issues'], 'facts': [f for f in combined['facts'] if fact_current(f)],
        'recalled':recalled, 'historical_navigation':navigation, 'target_answer':trace,
        'runtime_guidance':harness_experience.load(session,pk,query),
        'historical_facts': [f for f in combined['facts'] if f.get('active') and not fact_current(f)],
        'operator_sources': sources, 'requests': workspace.get('requests', []),
        'operator_reported_handled': any(t.get('execution') == 'operator_reported'
            and t.get('source_message_id') == inbound for t in state['rounds']),
        'knowledge_references': list({f"{r['source']}:{r['id']}": r for turn in state['rounds']
            for r in turn.get('knowledge', {}).get('references', [])}.values())[-40:]}
    if kind == 'operator' and trace:
        # A selected old answer must retain its v1 reference, not silently bind current v2.
        refs = {f"{r['source']}:{r['id']}":r for r in result['knowledge_references']}
        refs.update({f"{r['source']}:{r['id']}":r for r in trace.get('knowledge',{}).get('references',[])})
        result['knowledge_references']=list(refs.values())
    if len(json.dumps(result, ensure_ascii=False).encode()) > 256000:
        raise control.ControlConflict('会话业务上下文超过本轮预算，请人工整理，不会静默丢弃事实')
    return result


def fact_current(fact):
    if not fact.get('active'):
        return False
    if fact.get('lifetime') != 'temporary':
        return True
    # 临时状态只代表当日人工确认，不是永久库存；旧记录缺时刻时不猜日期。
    try:
        return datetime.fromisoformat(fact['confirmed_at']).date() == now_beijing_naive().date()
    except (KeyError, ValueError, TypeError):
        return False


def _checked_row(session, pk, uid, owner, snapshot, mode):
    shop = session.get(Shop, pk)
    if not shop or shop.status != 1 or shop.merged_into_shop_pk is not None or shop.owner_user_id != owner:
        raise control.ControlConflict('店铺状态或归属已变化')
    row = ai_workspace.row_for(session, pk, uid)
    if row is None and mode == 'auto' and snapshot['revision'] == 0:
        control.change(session, pk, uid, 'auto', expected_revision=0)
        row = ai_workspace.row_for(session, pk, uid)
        expected = 1
    else:
        expected = snapshot['revision']
    if (not row or row.mode != mode or row.revision != expected
        or ai_workspace.latest_inbound(session, pk, uid) != snapshot['source_message_id']
        or ai_workspace.latest_message(session, pk, uid, lock=True) != snapshot['boundary']):
        raise control.ControlConflict('业务上下文或人工控制已变化，旧决定未执行')
    return row


def _update(state, decision, snapshot):
    state = copy.deepcopy(state)
    issues = {i['id']: i for i in state['issues']}
    facts = {f['id']: f for f in state['facts']}
    sources = {s['id']: s for s in snapshot['operator_sources']}
    touched = []
    planned = {}
    aliases = {}
    for index, item in enumerate(decision['issues']):
        ident = item['id'] or next((i['id'] for i in issues.values()
            if i['topic'] == item['topic'] and i['object_ref'] == item['object_ref']
            and (item['object_ref'] or i['source_message_id'] == snapshot['source_message_id'])), None) or str(uuid.uuid4())
        planned[index] = ident
        if item.get('local_ref'):
            aliases[item['local_ref']] = ident
    for index, original_issue in enumerate(decision['issues']):
        d = dict(original_issue)
        d['related_ids'] = list(dict.fromkeys(aliases.get(i, i) for i in d.get('related_ids', [])))
        ident = d['id']
        if any(i not in issues and i not in planned.values() for i in d.get('related_ids', [])):
            raise ValueError('关联规划已变化')
        if ident and ident not in issues:
            raise ValueError('业务问题引用不存在')
        if ident and issues[ident]['object_ref'] and d['object_ref'] != issues[ident]['object_ref']:
            raise ValueError('商品或规格改变时应新建关联问题，不覆盖旧对象')
        if not ident:
            ident = planned[index]
        if ident in touched:
            raise ValueError('同一问题出现冲突决定')
        previous = issues.get(ident, {})
        known_requests = {r['id'] for r in snapshot.get('requests', [])}
        if d.get('request_id') and d['request_id'] not in known_requests and d['request_id'] != previous.get('request_id'):
            raise ValueError('待补事项引用不存在')
        statuses = {'reply': 'ready', 'ask_customer': 'waiting_customer',
            'request_staff': 'waiting_staff', 'handoff': 'waiting_staff', 'resolve': 'resolved'}
        status = statuses.get(d['action'], previous.get('status', 'pending'))
        issues[ident] = {**{k:v for k,v in previous.items() if k != 'local_ref'},
            **{k:v for k,v in d.items() if k != 'local_ref'}, 'id': ident, 'status': status,
            'source_message_id': snapshot['source_message_id'],
            'origin_message_id': previous.get('origin_message_id', previous.get('source_message_id', snapshot['source_message_id'])),
            'source_message_ids': list(dict.fromkeys([*previous.get('source_message_ids', []),
                *[i for i in (previous.get('source_message_id'), snapshot['source_message_id']) if i]])),
            'request_id': d.get('request_id') or previous.get('request_id', '')}
        touched.append(ident)
    extracted = []
    for original in decision['facts']:
        f = dict(original)
        # 代词没有商品ID时绑定原问题，不伪造商品身份，更不泛化为全店。
        f['object_ref'] = f['object_ref'] or 'unresolved:' + str(snapshot['source_message_id'])
        if snapshot['event']['kind'] != 'operator' or f['source_id'] not in sources:
            raise ValueError('事实缺少真实人工来源')
        refs = {f"{r['source']}:{r['id']}": r for r in snapshot.get('knowledge_references', [])}
        if f.get('corrects_knowledge') and f['corrects_knowledge'] not in refs:
            raise ValueError('被纠正知识未在真实上下文中引用')
        corrected = refs.get(f.get('corrects_knowledge'))
        if corrected and corrected['source'] not in ('pdd_product_knowledge','pdd_customer_service_knowledge'):
            raise ValueError('同步目录不是可修订的知识版本，请作为新候选人工核对')
        old = facts.get(f['supersedes']) if f['supersedes'] else None
        if f['supersedes'] and (not old or old['object_ref'] != f['object_ref']):
            raise ValueError('事实纠正对象或范围无效')
        # 相同来源、范围和正文重跑沿用同一个事实及提炼任务。
        ident = str(uuid.uuid5(uuid.NAMESPACE_URL, json.dumps(f, sort_keys=True, ensure_ascii=False)))
        if old and old['id'] != ident:
            old['active'] = False; old['replaced_by'] = ident
        current = {**f, 'id': ident, 'active': True, 'source_text': sources[f['source_id']]['text'], 'actor_id': sources[f['source_id']].get('actor_id'),
            'confirmed_at': sources[f['source_id']].get('at'),
            'corrected_reference': refs.get(f.get('corrects_knowledge'))}
        if ident not in facts:
            facts[ident] = current; extracted.append(current)
    state['issues'] = list(issues.values()); state['facts'] = list(facts.values())
    return state, touched, extracted


def _requests(session, row, state, touched, snapshot):
    value = human_workspace.data(row) or {}
    requests = {r['id']: dict(r) for r in value.get('requests', [])}
    for item in state['issues']:
        if item['id'] not in touched and item['status'] != 'waiting_staff':
            continue
        rid = item.get('request_id')
        if rid and rid not in requests:
            # 问题跨人工批次继续存在，旧待补ID可重建展示，不新增业务问题。
            requests[rid] = {'id':rid, 'issue_id':item['id'], 'question':item['question'],
                'status':'open' if item['status']=='waiting_staff' else 'resolved',
                'source_message_id':item['source_message_id']}
        if item['action'] in ('request_staff', 'handoff'):
            rid = rid or next((r['id'] for r in requests.values()
                if r.get('issue_id') == item['id'] or r['question'] == item['question']), None) or str(uuid.uuid4())
            item['request_id'] = rid
            requests[rid] = {'id': rid, 'issue_id': item['id'], 'question': item['question'],
                'status': 'open', 'source_message_id': snapshot['source_message_id']}
        elif rid and item['action'] != 'no_reply':
            requests[rid]['status'] = 'withdrawn' if item['action'] == 'ask_customer' else 'resolved'
    closed=[r for r in requests.values() if r.get('status')!='open']
    if len(closed)>16:
        from common.services import context_archive
        for r in closed[:-16]:
            context_archive.put(session,row.shop_pk,row.customer_uid,'request',r['id'],r)
            requests.pop(r['id'],None)
    if len(requests)>128:
        raise control.ControlConflict('当前待补事项超过处理预算，原输入已保留')
    value['requests'] = list(requests.values()); value['assessed'] = True
    return value


def _curate(session, row, facts, value):
    shop = session.get(Shop, row.shop_pk)
    analyzed_before = set(value.get('analyzed_source_ids', []))
    for f in facts:
        if f['source_id'] in analyzed_before or f['lifetime'] != 'reusable':
            continue
        # 不修改正式知识。源事实的固定ID复用原提炼表唯一批次键。
        key = f['id']
        if session.scalar(select(KnowledgeCuration.id).where(KnowledgeCuration.batch_key == key)):
            continue
        contribution = {'id': f['id'], 'content': f['text'], 'scope': f['scope'],
            'object_ref': f['object_ref'], 'source_entry_id': f['source_id'],
            'category': f.get('category'), 'supersedes': f['supersedes'], 'corrected_reference': f.get('corrected_reference')}
        session.add(KnowledgeCuration(shop_pk=row.shop_pk, owner_user_id=shop.owner_user_id,
            customer_uid=row.customer_uid, batch_key=key, state='pending',
            snapshot_json=json.dumps({'contributions': [contribution], 'delivery_ids': []}, ensure_ascii=False),
            created_by=f.get('actor_id')))
    # 已经结构化判断的来源不再在结束人工时按原文重复提炼，包括临时事实。
    done = set(value.get('analyzed_source_ids', []))
    done.update(f['source_id'] for f in facts)
    value['analyzed_source_ids'] = list(done)


def apply(session, pk, uid, owner, snapshot, decision, *, audience, evidence=None):
    mode = 'auto' if snapshot['event']['kind'] == 'customer' else 'human'
    row = _checked_row(session, pk, uid, owner, snapshot, mode)
    from common.services.message_delivery import current_inbox, assert_lease
    handle = current_inbox.get()
    if handle:
        assert_lease(session, handle, pk, uid)
    from common.services.operator_commands import stop_sending
    current = next((s for s in snapshot['operator_sources'] if s['id'] == snapshot['event']['source_id']), None)
    if current and stop_sending(current['text']):
        decision = copy.deepcopy(decision)
        decision['customer_reply'] = ''
        for item in decision['issues']:
            if item['action'] in ('reply', 'ask_customer'):
                item['action'] = 'no_reply'
    from common.services.harness_references import normalize
    decision = normalize(decision, snapshot)
    from common.services import context_archive, harness_experience
    harness_experience.validate_loaded(session,pk,snapshot.get('runtime_guidance',[]))
    previous=read(row)
    context_archive.preserve_rounds(session,row,previous)
    restored = context_archive.restore(previous, snapshot)
    state, touched, extracted = _update(restored, decision, snapshot)
    for item in decision.get('observations', []):
        src = next((s for s in snapshot['operator_sources'] if s['id']==item['source_id']),None)
        if snapshot['event']['kind']!='operator' or not src:
            raise ValueError('运行经验缺少真实人工来源')
        harness_experience.capture(session,pk,uid,'operator:'+src['id']+':'+item['category']+':'+item['title'],item['category'],item['title'],
            item['observation'],source={'entry_id':src['id'],'answer_id':(snapshot.get('target_answer') or {}).get('id'),'policy_version':snapshot.get('policy_version'),'model':snapshot.get('model')},
            guidance=item['guidance'],trigger_terms=item['trigger_terms'])
    from common.services.operator_commands import reported_sent
    handled = bool(current and reported_sent(current['text']))
    if handled:
        for issue in state['issues']:
            if issue['source_message_id'] == snapshot['source_message_id'] and issue['status'] == 'ready':
                issue['status'] = 'answered'
                issue['answer_source'] = {'kind':'operator_report', 'id':current['id']}
    # 自动模式也保留待补，之后由原接管服务改变归属。
    human_workspace.ensure_batch(session, row)
    value = _requests(session, row, state, touched, snapshot)
    _curate(session, row, extracted, value)
    row.workspace_json = json.dumps(value, ensure_ascii=False)
    round_id = str(uuid.uuid4())
    for issue in state['issues']:
        if issue['id'] in touched: issue['last_round_id'] = round_id
    turn = {'id': round_id, 'event': snapshot['event'],
        'source_message_id': snapshot['source_message_id'], 'boundary': snapshot['boundary'],
        'issues': touched, 'actions': [d['action'] for d in decision['issues']],
        'knowledge': evidence or {}, 'reference_warnings': decision.get('reference_warnings', []), 'execution': 'operator_reported' if handled else 'not_sent', 'inbox_id': handle[0] if handle else None,
        'customer_reply':decision['customer_reply'],'operator_message':decision['message'],
        'operator_sources':snapshot['operator_sources'],
        'policy_version':snapshot.get('policy_version'),'model':snapshot.get('model'),
        'runtime_guidance':[{k:g[k] for k in ('id','version')} for g in snapshot.get('runtime_guidance',[])]}
    context_archive.put(session,pk,uid,'answer',round_id,turn,inbox_id=handle[0] if handle else None)
    hot_turn={k:v for k,v in turn.items() if k not in ('operator_sources','customer_reply','operator_message','runtime_guidance')}
    state['rounds'] = (state['rounds'] + [hot_turn])[-16:]
    state=context_archive.compact(session,row,state)
    if len(state['issues'])>128 or len(state['facts'])>256 or len(ai_workspace.history_anchors(state['issues']))>512:
        raise control.ControlConflict('当前未完成事项超过处理预算，原输入已保留，请人工整理')
    row.harness_json = json.dumps(state, ensure_ascii=False)
    row.revision += 1; row.draft_json = None
    if mode == 'human':
        message = decision['message'] or '已更新当前问题的处理依据。'
        if decision.get('reference_warnings'):
            message += '\n补充内容已保留；有个别问题关联未能确认，未据此修改其他问题。'
        human_workspace.append(session, row, 'assistant', message,
            source_message_id=snapshot['source_message_id'])
        if decision['customer_reply']:
            # 同一决定形成草稿，不再第二次模型调用。
            row.draft_json = json.dumps({'format_version': 2, 'history_boundary_id': snapshot['boundary'],
                'content': decision['customer_reply'], 'operator_note': '', 'source_message_id': snapshot['source_message_id'],
                'control_revision': row.revision, 'knowledge': evidence or {}, 'business_round_id': round_id}, ensure_ascii=False)
    row.attention_count = control.view(row)['attention_count']
    session.flush()
    return {'content': decision['customer_reply'] if audience == 'draft' else decision['message'],
        'control_revision': row.revision, 'business_round_id': round_id, 'source_message_id': snapshot['source_message_id'],
        'history_boundary_id': snapshot['boundary'], 'reference_warnings': decision.get('reference_warnings', []), 'needs_staff': any(i['status'] == 'waiting_staff' for i in state['issues'])}


def execution(session, pk, uid, round_id, result, *, delivery_key=None):
    row = ai_workspace.row_for(session, pk, uid)
    if not row:
        return
    state = read(row)
    found = next((r for r in state['rounds'] if r['id'] == round_id), None)
    from common.services import context_archive
    archived=context_archive.latest(session,pk,uid,'answer',round_id)
    if archived:
        if delivery_key: archived.delivery_key=delivery_key
        # Original delivery ledger is authoritative; execution record is append-only.
        context_archive.put(session,pk,uid,'answer_execution',round_id,
            {'round_id':round_id,'result':result,'delivery_key':delivery_key},inbox_id=archived.inbox_id)
    if not found:
        return
    found['execution'] = result
    if delivery_key: found['delivery_key'] = delivery_key
    if result in ('accepted', 'confirmed'):
        for issue in state['issues']:
            if issue.get('last_round_id') == round_id and issue['status'] == 'ready':
                issue['status'] = 'answered'
    row.harness_json = json.dumps(state, ensure_ascii=False)


def reconcile(session, row):
    """只读本地原发送账恢复业务进度，不向平台补发或查询。"""
    if not row or not row.harness_json:
        return
    from common.models.message_delivery_models import MessageDelivery
    from common.services.media_handoff import reconcile_manual
    reconcile_manual(session,row)
    state = read(row)
    for turn in state['rounds']:
        if turn['execution'] in ('accepted', 'confirmed'):
            continue
        query = select(MessageDelivery).where(MessageDelivery.shop_pk == row.shop_pk,
            MessageDelivery.customer_uid == row.customer_uid)
        if turn.get('delivery_key'):
            query = query.where(MessageDelivery.operation_key == turn['delivery_key'])
        elif turn.get('inbox_id'):
            query = query.where(MessageDelivery.inbox_id == turn['inbox_id'])
        else:
            continue
        delivery = session.scalar(query.order_by(MessageDelivery.id.desc()).limit(1))
        if delivery and delivery.state in ('accepted','confirmed'):
            execution(session,row.shop_pk,row.customer_uid,turn['id'],delivery.state)


def operator_event_decision(snapshot):
    """内部确认/已回复说明不是顾客新事件，不重跑旧问题，也不自动发消息。"""
    from common.services.operator_commands import acknowledgement, reported_sent
    if snapshot['event']['kind'] != 'operator' or snapshot['event'].get('purpose') != 'advice':
        return None
    source = next((s for s in snapshot['operator_sources'] if s['id'] == snapshot['event']['source_id']), None)
    if not source:
        return None
    if reported_sent(source['text']):
        message = '已记录你已回复，本轮不再重复拟回复。其他待办保持原样。'
    elif acknowledgement(source['text']):
        message = '收到。你这条确认不会作为顾客的新消息，也不会触发重复回复。'
    else:
        return None
    return {'version':VERSION, 'message':message, 'customer_reply':'', 'issues':[], 'facts':[]}
