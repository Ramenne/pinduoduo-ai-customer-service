"""人工批次与内部协作。控制行锁是唯一写入串行点，历史不属于对客消息。"""
import json
import uuid
from sqlalchemy import select, func
from common.models.message_delivery_models import MessageDelivery
from common.models.knowledge_curation_models import KnowledgeCuration
from common.models.shop_models import Shop
from common.services.conversation_control import ControlConflict
from common.utils.time_utils import now_beijing_naive, safe_isoformat


def data(row):
    return json.loads(row.workspace_json) if row and row.workspace_json else None


def entries(value):
    """批次结束只结束操作权，不删除内部原文；历史内容不成为当前授权。"""
    if not value:
        return []
    return [dict(item, archived=True, batch_key=batch['batch_key'])
        for batch in value.get('archives', []) for item in batch.get('entries', [])] + value.get('entries', [])


def archive_batch(session,row,value):
    from common.services import context_archive
    original={k:v for k,v in value.items() if k!='archives'}
    entries=original.get('entries',[])
    if len(entries)>32:
        context_archive.put(session,row.shop_pk,row.customer_uid,'human_raw',original['batch_key'],original)
        for start in range(0,len(entries),24):
            part=entries[start:start+24]
            context_archive.put(session,row.shop_pk,row.customer_uid,'human_segment',part[0]['id'],
                {'batch_key':original['batch_key'],'entries':part,'closed':True,'historical_only':True},summarize=len(part)>=12)
    else:
        context_archive.put(session,row.shop_pk,row.customer_uid,'human_batch',original['batch_key'],original,summarize=len(entries)>=12)


def migrate_archives(session, row):
    value=data(row)
    if not value or not value.get('archives'):return value
    for old in value['archives']:
        archive_batch(session,row,old)
    value['archives']=[]
    row.workspace_json=json.dumps(value,ensure_ascii=False)
    session.flush()
    return value


def ensure_batch(session, row):
    value = migrate_archives(session,row)
    if value and not value.get('closed'):
        return value
    if value:
        archive_batch(session,row,value)
    value = {'batch_key': str(uuid.uuid4()), 'entries': [], 'closed': False, 'archives':[],
        'start_delivery_id': session.scalar(select(func.max(MessageDelivery.id)).where(
            MessageDelivery.shop_pk == row.shop_pk, MessageDelivery.customer_uid == row.customer_uid)) or 0}
    # 保留0.1.8尚未结束的事实补充；新一批不得复用上一批。
    legacy = json.loads(row.supplement_text) if row.supplement_text else None
    if legacy and legacy.get('revision') == row.revision:
        value['entries'].append(entry('fact', legacy['text'], row.created_by))
    row.workspace_json = json.dumps(value, ensure_ascii=False)
    return value


def entry(kind, content, actor_id=None, **extra):
    return {'id': str(uuid.uuid4()), 'kind': kind, 'content': content,
        'actor_id': actor_id, 'at': safe_isoformat(now_beijing_naive()), **extra}


def append(session, row, kind, content, actor_id=None, **extra):
    value = ensure_batch(session, row)
    # Atomic cold segment before eviction; analyzed facts remain in shared Harness state.
    if len(value['entries']) >= 40 or sum(len(x['content']) for x in value['entries']) + len(content) > 50000:
        from common.services import context_archive
        old=value['entries'][:-16]
        if old:
            segment={'batch_key':value['batch_key'],'entries':old,'historical_only':True}
            saved=context_archive.put(session,row.shop_pk,row.customer_uid,'human_segment',old[0]['id'],segment,summarize=True)
            analyzed=set(value.get('analyzed_source_ids',[]))
            contributions=[e for e in old if e['kind'] in ('fact','instruction') and e['id'] not in analyzed and not operation_only(e['content'])]
            if contributions:
                shop=session.get(Shop,row.shop_pk)
                key=str(uuid.uuid5(uuid.NAMESPACE_URL,'human-segment:'+str(saved.id)))
                if not session.scalar(select(KnowledgeCuration.id).where(KnowledgeCuration.batch_key==key)):
                    session.add(KnowledgeCuration(shop_pk=row.shop_pk,owner_user_id=shop.owner_user_id,
                        customer_uid=row.customer_uid,batch_key=key,state='pending',
                        snapshot_json=json.dumps({'contributions':contributions,'delivery_ids':[]},ensure_ascii=False)))
            value['entries']=value['entries'][-16:]
            retained={e['id'] for e in value['entries']}
            value['analyzed_source_ids']=[i for i in analyzed if i in retained]
    if len(content)>10000:
        raise ControlConflict('单条协作内容过长，请分段补充')
    value['entries'].append(entry(kind, content, actor_id, **extra))
    row.workspace_json = json.dumps(value, ensure_ascii=False)
    return value


def facts(value):
    return '\n'.join(x['content'] for x in (value or {}).get('entries', []) if x['kind'] == 'fact')


def finish(session, row):
    """与恢复自动的控制变更同一事务；重复结束不产生重复任务。"""
    value = data(row) or ensure_batch(session, row)
    if value.get('closed'):
        return
    deliveries = list(session.scalars(select(MessageDelivery).where(
        MessageDelivery.shop_pk == row.shop_pk, MessageDelivery.customer_uid == row.customer_uid,
        MessageDelivery.id > value['start_delivery_id'], MessageDelivery.operation_key.like('manual:%'),
        MessageDelivery.kind == 'text', MessageDelivery.state.in_(['confirmed', 'accepted']))))
    analyzed = set(value.get('analyzed_source_ids', []))
    contributions = [x for x in value['entries'] if x['kind'] in ('fact','instruction')
        and x['id'] not in analyzed and not operation_only(x['content'])]
    # 失败分析的自然语言也保留给原提炼器；只作为资料，不把操作要求批准成知识。
    if contributions or deliveries:
        shop = session.get(Shop, row.shop_pk)
        snapshot = {'contributions': contributions, 'delivery_ids': [d.id for d in deliveries]}
        session.add(KnowledgeCuration(shop_pk=row.shop_pk, owner_user_id=shop.owner_user_id,
            customer_uid=row.customer_uid, batch_key=value['batch_key'],
            snapshot_json=json.dumps(snapshot, ensure_ascii=False), state='pending', created_by=row.created_by))
    value['closed'] = True
    row.workspace_json = json.dumps(value, ensure_ascii=False)
    # 原字段保留作历史依据；revision/mode使它失效，新批次另行清空。
    row.draft_json = None


def operation_only(text):
    from common.services.operator_commands import acknowledgement, explicitly_send, reported_sent
    if acknowledgement(text) or explicitly_send(text) or reported_sent(text):
        return True
    import re
    return bool(re.fullmatch(r'\s*(?:请|帮我|麻烦)?(?:重新分析|重新生成|检查一下|不要发送|不用再发|先别发)[。！!\s]*', text))


def curation_view(session, shop_pk, uid):
    from common.services.knowledge_publication import candidates_view
    shop = session.get(Shop, shop_pk)
    rows = session.scalars(select(KnowledgeCuration).where(KnowledgeCuration.shop_pk == shop_pk,
        KnowledgeCuration.customer_uid == uid, KnowledgeCuration.owner_user_id == shop.owner_user_id)
        .order_by(KnowledgeCuration.id.desc()).limit(5))
    return [{'id': r.id, 'state': r.state, 'attempts': r.attempts, 'error': r.error,
        'candidates': candidates_view(r)} for r in rows]
