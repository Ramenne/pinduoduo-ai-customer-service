"""Scoped immutable cold context. Original inbox/outbox and control remain authoritative."""
import copy
import hashlib
import json
import re
from sqlalchemy import select, or_, func
from common.models.harness_archive_models import ContextArchive as Archive
from common.models.shop_models import Shop


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def scope(session, pk, uid=None):
    shop = session.get(Shop, pk)
    if not shop or shop.merged_into_shop_pk is not None:
        raise ValueError('店铺不存在')
    conditions = [Archive.shop_pk == pk, Archive.owner_user_id == shop.owner_user_id]
    if uid is not None:
        conditions.append(Archive.customer_uid == str(uid))
    return shop, conditions


def put(session, pk, uid, kind, stable_id, payload, *, summarize=False, inbox_id=None):
    shop, conditions = scope(session, pk, uid)
    raw = encode(payload)
    digest = hashlib.sha256(raw.encode()).hexdigest()
    row = session.scalar(select(Archive).where(*conditions, Archive.kind == kind,
        Archive.stable_id == stable_id, Archive.source_hash == digest))
    if row is None:
        row = Archive(shop_pk=pk, owner_user_id=shop.owner_user_id, customer_uid=str(uid),
            kind=kind, stable_id=stable_id, source_hash=digest, payload_json=raw,
            retrieval_text=encode({k:v for k,v in payload.items() if k!='operator_sources'})[:16000] if kind=='answer' else raw[:16000], state='pending' if summarize else 'stored', inbox_id=inbox_id)
        session.add(row)
        session.flush()  # commit raw before caller evicts hot state, same outer transaction
    return row


def latest(session, pk, uid, kind, stable_id):
    _, conditions = scope(session, pk, uid)
    return session.scalar(select(Archive).where(*conditions, Archive.kind == kind,
        Archive.stable_id == stable_id).order_by(Archive.id.desc()).limit(1))


def compact(session, row, state):
    """Keep unfinished issues and current facts. Only inactive/expired/unrelated facts move."""
    from common.services.harness_state import fact_current
    state = copy.deepcopy(state)
    active = [i for i in state['issues'] if i.get('status') not in ('answered', 'resolved')]
    protected = {i.get('object_ref') for i in active}
    protected.add(next((i.get('object_ref') for i in reversed(state['issues']) if i.get('object_ref')), ''))
    movable_issues = [i for i in state['issues'] if i.get('status') in ('answered', 'resolved')]
    movable_facts = [f for f in state['facts'] if not fact_current(f)
        or (f.get('object_ref') not in protected and f.get('object_ref') != 'shop')]
    removed = []
    for key, candidates, target in (('issues', movable_issues, 32), ('facts', movable_facts, 64)):
        threshold=48 if key=='issues' else 96
        count = max(0, len(state[key]) - target) if len(state[key])>=threshold else 0
        for item in candidates[:count]:
            kind = 'state_issue' if key == 'issues' else 'state_fact'
            saved = put(session, row.shop_pk, row.customer_uid, kind, item['id'], {**item, '_archive_revision':row.revision})
            removed.append({'archive_id': saved.id, 'kind':kind, 'id':item['id'], 'value':item})
        ids = {x['id'] for x in removed if x['kind'] == ('state_issue' if key == 'issues' else 'state_fact')}
        state[key] = [x for x in state[key] if x['id'] not in ids]
    for start in range(0, len(removed), 16):
        segment = removed[start:start+16]
        put(session, row.shop_pk, row.customer_uid, 'state_segment',
            hashlib.sha256(encode(segment).encode()).hexdigest(), {'items':segment}, summarize=len(segment)>=8 or len(encode(segment))>12000)
    return state


def terms(query):
    # Object/order IDs first; Chinese phrases plus bigrams allow topic lookup without a vector service.
    parts = re.findall(r'[a-zA-Z0-9_:-]{2,80}|[\u4e00-\u9fff]{2,30}', query or '')[:24]
    return list(dict.fromkeys(parts + [p[i:i+2] for p in parts if re.search(r'[\u4e00-\u9fff]',p)
        for i in range(len(p)-1)]))[:48]


def recall(session, pk, uid, query, state, *, answer_id=None):
    _, conditions = scope(session, pk, uid)
    words = terms(query)
    matched = []
    precise=[w for w in words if any(c.isdigit() for c in w)]
    if precise:
        matched=list(session.scalars(select(Archive).where(*conditions,Archive.kind.in_(['state_issue','state_fact','state_segment','answer']),
            or_(*(Archive.payload_json.contains(w,autoescape=True) for w in precise)))
            .order_by(Archive.id.desc()).limit(48)))
    if words:
        matched += list(session.scalars(select(Archive).where(*conditions,
            Archive.kind.in_(['state_issue','state_fact','state_segment','human_segment','human_batch','answer']),
            or_(*(Archive.payload_json.contains(w, autoescape=True) for w in words)))
            .order_by(Archive.id.desc()).limit(64)))
    # Exact object references score above incidental topic words.
    def score(r):
        return sum((12 if any(c.isdigit() for c in w) else max(1,len(w)-1))
            for w in words if w in (r.retrieval_text if r.kind=='answer' else r.payload_json))
    matched=list({r.id:r for r in matched}.values())
    matched.sort(key=lambda r:(score(r),r.id), reverse=True)
    restored = {'issues':[], 'facts':[]}
    navigation, seen, size = [], set(), 0
    for item in matched[:12]:
        raw = json.loads(item.payload_json)
        targets = [(item.kind, item.stable_id)] if item.kind in ('state_issue','state_fact') else [
            (v['kind'],v['id']) for v in raw.get('items',[]) if isinstance(v,dict) and v.get('kind') in ('state_issue','state_fact')]
        by_id={v['id']:encode(v.get('value',{})) for v in raw.get('items',[]) if isinstance(v,dict) and v.get('id')}
        targets.sort(key=lambda pair:sum((12 if any(c.isdigit() for c in w) else max(1,len(w)-1)) for w in words if w in by_id.get(pair[1],'')),reverse=True)
        for kind, ident in targets:
            if (kind,ident) in seen: continue
            seen.add((kind,ident))
            key = 'issues' if kind == 'state_issue' else 'facts'
            if any(x['id']==ident for x in state[key]): continue
            current = latest(session,pk,uid,kind,ident)
            value = json.loads(current.payload_json)
            if size + len(encode(value)) > 22000: continue
            size += len(encode(value)); restored[key].append(value)
        if item.kind in ('human_segment','human_batch'):
            all_entries=[e for e in raw.get('entries',[]) if e.get('kind') in ('fact','instruction','assistant')]
            # Retrieve the matching early contribution, not an arbitrary tail of the batch.
            ranked=sorted(enumerate(all_entries),key=lambda pair:(sum((12 if any(c.isdigit() for c in w) else max(1,len(w)-1))
                for w in words if w in pair[1].get('content','')),pair[0]),reverse=True)[:8]
            selected=[]
            for index,entry in ranked:
                trial=sorted(selected+[(index,entry)],key=lambda pair:pair[0])
                snippet={'archive_id':item.id,'historical_only':True,'entries':[e for _,e in trial]}
                if size+len(encode(snippet))<=22000:selected=trial
            if selected:
                snippet={'archive_id':item.id,'historical_only':True,'entries':[e for _,e in selected]}
                size+=len(encode(snippet));navigation.append(snippet)
        elif item.summary_json:
            snippet={'archive_id':item.id,'historical_only':True,'summary':json.loads(item.summary_json)}
            if size+len(encode(snippet))<=22000:
                size+=len(encode(snippet));navigation.append(snippet)
    # Explicit selection wins. Otherwise prefer latest actual answer over internal acknowledgements.
    answer = latest(session,pk,uid,'answer',answer_id) if answer_id else None
    if not answer_id:
        recent=list(session.scalars(select(Archive).where(*conditions,Archive.kind=='answer')
            .order_by(Archive.id.desc()).limit(24)))
        candidates=[r for r in recent if json.loads(r.payload_json).get('customer_reply')]
        answer=next(iter(candidates),next(iter(recent),None))
        relevant=[r for r in matched if r.kind=='answer' and json.loads(r.payload_json).get('customer_reply')]
        # Only explicit object/number references select an earlier answer without UI selection.
        if precise and relevant:answer=max(relevant,key=lambda r:(score(r),r.id))
    trace = trace_view(session,pk,uid,answer) if answer else None
    if trace:
        trace={k:v for k,v in trace.items() if k not in ('operator_sources',)}
        if trace.get('actual_delivery'):trace['execution']=trace['actual_delivery']['state']
    if answer_id and not answer:
        raise ValueError('所选回答不存在或不属于当前会话')
    return restored, navigation[:6], trace


def restore(state, snapshot):
    state = copy.deepcopy(state)
    for key in ('issues','facts'):
        known = {v['id'] for v in state[key]}
        state[key] += [copy.deepcopy(v) for v in snapshot.get('recalled',{}).get(key,[]) if v['id'] not in known]
    return state


def history_page(session, pk, uid, *, before=None, limit=20):
    _, conditions = scope(session,pk,uid)
    query = select(Archive).where(*conditions, Archive.kind.in_(['human_batch','human_segment']))
    if before: query = query.where(Archive.id < before)
    rows = list(session.scalars(query.order_by(Archive.id.desc()).limit(limit+1)))
    selected = rows[:limit]
    entries = [dict(e, archived=True, batch_key=json.loads(r.payload_json).get('batch_key'))
        for r in reversed(selected) for e in json.loads(r.payload_json).get('entries',[])]
    return {'entries':entries,'before':selected[-1].id if len(rows)>limit and selected else None}


def trace_view(session, pk, uid, row):
    from common.models.message_delivery_models import MessageDelivery
    payload = json.loads(row.payload_json)
    query = select(MessageDelivery).where(MessageDelivery.shop_pk==pk,MessageDelivery.customer_uid==str(uid))
    key = row.delivery_key or payload.get('delivery_key')
    if key: query=query.where(MessageDelivery.operation_key==key)
    elif row.inbox_id: query=query.where(MessageDelivery.inbox_id==row.inbox_id)
    else: query=None
    delivery=session.scalar(query.order_by(MessageDelivery.id.desc()).limit(1)) if query is not None else None
    return {**payload,'archive_id':row.id,'source_hash':row.source_hash,
        'recorded_at':row.created_at.isoformat() if row.created_at else None,
        'actual_delivery': {'id':delivery.id,'state':delivery.state,'operation_key':delivery.operation_key,
            'body':json.loads(delivery.body)} if delivery else None}


def trace_page(session, pk, uid, *, before=None, limit=20):
    _, conditions=scope(session,pk,uid)
    query=select(Archive).where(*conditions,Archive.kind=='answer')
    if before: query=query.where(Archive.id<before)
    rows=list(session.scalars(query.order_by(Archive.id.desc()).limit(limit+1)))
    return {'items':[trace_view(session,pk,uid,r) for r in rows[:limit]],
        'before':rows[limit-1].id if len(rows)>limit else None}


def preserve_rounds(session,row,state):
    """Import only the surviving pre-upgrade rounds, without inventing missing evidence."""
    from common.models.message_delivery_models import MessageDelivery
    _,conditions=scope(session,row.shop_pk,row.customer_uid)
    turns=state.get('rounds',[])
    existing=set(session.scalars(select(Archive.stable_id).where(*conditions,Archive.kind=='answer',
        Archive.stable_id.in_([t['id'] for t in turns])))) if turns else set()
    for turn in turns:
        if turn['id'] in existing:continue
        saved=copy.deepcopy(turn);saved['legacy_import']=True
        draft=json.loads(row.draft_json) if row.draft_json else {}
        delivery=None
        q=select(MessageDelivery).where(MessageDelivery.shop_pk==row.shop_pk,
            MessageDelivery.customer_uid==row.customer_uid,MessageDelivery.kind=='text')
        if turn.get('delivery_key'):q=q.where(MessageDelivery.operation_key==turn['delivery_key'])
        elif turn.get('inbox_id'):q=q.where(MessageDelivery.inbox_id==turn['inbox_id'])
        else:q=None
        if q is not None:delivery=session.scalar(q.order_by(MessageDelivery.id.desc()).limit(1))
        if delivery:
            body=json.loads(delivery.body)
            saved['customer_reply']=body if isinstance(body,str) else ''
        elif draft.get('business_round_id')==turn['id']:saved['customer_reply']=draft.get('content','')
        else:saved['customer_reply']=''
        saved['provenance_notice']='升级前仅导入仍留存的轮次依据；缺失正文、模型或规则版本不补造。'
        archived=put(session,row.shop_pk,row.customer_uid,'answer',turn['id'],saved,inbox_id=turn.get('inbox_id'))
        if delivery:archived.delivery_key=delivery.operation_key
