"""复用会话控制行保存人工补充及待确认建议，不拥有平台发送权。"""
import json
import math
from datetime import datetime, timezone, timedelta
from sqlalchemy import select, func, or_, and_
from common.models.conversation_control_models import ConversationControl
from common.models.log_models import ChatMessage
from common.models.shop_models import Shop
from common.services import conversation_control as control
from common.services import human_workspace


NOTICE_TYPES = ('system_status', 'auth', 'mall_system_msg')


def message_time():
    return func.coalesce(ChatMessage.msg_time, ChatMessage.created_at)


def relevant_messages(shop_pk, customer_uid):
    return select(ChatMessage).where(ChatMessage.shop_pk == shop_pk,
        ChatMessage.customer_uid == str(customer_uid), ChatMessage.direction.in_(['in', 'out']),
        (ChatMessage.msg_type.is_(None) | ChatMessage.msg_type.notin_(NOTICE_TYPES)))


def latest_message_row(session, shop_pk, customer_uid, *, inbound=False,lock=False):
    query = relevant_messages(shop_pk, customer_uid)
    if inbound:
        query = query.where(ChatMessage.direction == 'in')
    query=query.order_by(message_time().desc(), ChatMessage.id.desc()).limit(1)
    if lock:query=query.with_for_update().execution_options(populate_existing=True)
    return session.scalar(query)


def latest_inbound(session, shop_pk, customer_uid):
    row = latest_message_row(session, shop_pk, customer_uid, inbound=True)
    return row.id if row else None


def latest_message(session, shop_pk, customer_uid, *, lock=False):
    # ID是写入顺序，不是对话顺序。历史回填和平台通知不取消当前建议。
    row = latest_message_row(session, shop_pk, customer_uid,lock=lock)
    return row.id if row else 0


def platform_time(value):
    """平台时刻与接收时刻分开保存；未知时刻不伪造。"""
    try:
        stamp = float(value or 0)
        if not math.isfinite(stamp) or stamp <= 0:
            return None
        if stamp > 100000000000:
            stamp /= 1000
        return datetime.fromtimestamp(stamp, timezone.utc).astimezone(
            timezone(timedelta(hours=8))).replace(tzinfo=None)
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def pending_event(session, pk, uid, message_id):
    """已持久化事件是否仍欠回复；查询失败/未落库不等于旧消息。"""
    source = session.scalar(relevant_messages(pk, uid).where(
        ChatMessage.direction == 'in', ChatMessage.msg_id == str(message_id)).limit(1))
    if source is None:
        raise control.ControlConflict('当前来话尚未可靠归档，稍后接续')
    newest = latest_message_row(session, pk, uid)
    inbound = latest_message_row(session, pk, uid, inbound=True)
    if not inbound or inbound.id != source.id:
        return 'superseded', source.id
    if newest.direction == 'out':
        return 'answered', source.id
    return 'current', newest.id


def history_anchors(issues):
    return list({ident for issue in issues for ident in (
        issue.get('origin_message_id'), issue.get('source_message_id'),
        *issue.get('source_message_ids', [])) if isinstance(ident, int) and ident > 0})


def message_content(row):
    """所有模型入口共用的有角色历史；卡片字段与原消息一起进入上下文。"""
    content = row.content or ''
    for label, raw in (('商品资料', row.goods_context), ('订单资料', row.order_context)):
        if raw:
            try:
                value = json.loads(raw)
            except (ValueError, TypeError) as exc:
                raise control.ControlConflict('历史商品或订单资料暂不可读') from exc
            if not isinstance(value, dict):
                raise control.ControlConflict('历史商品或订单资料格式无效')
            content += '\n【历史' + label + '】' + json.dumps(value, ensure_ascii=False)
    return content.strip()


def model_history(session, pk, uid, boundary, *, exclude_id=None, anchor_ids=(), limit=20):
    rows = history_through(session, pk, uid, boundary, limit=limit, anchor_ids=anchor_ids)
    history = []
    for row in rows:
        if row.id == exclude_id or str(row.content or '').startswith(
                ('不支持的消息类型:', '不支持的消息类型：', '[平台扩展消息]')):
            continue
        content = message_content(row)
        if content:
            history.append({'role':'assistant' if row.direction == 'out' else 'user', 'content':content})
    if sum(len(item['content']) for item in history) > 64000:
        raise control.ControlConflict('关联历史内容超过本轮预算，未生成回复')
    return history


def history_before(session, source, limit=10):
    from sqlalchemy import or_, and_
    at = source.msg_time or source.created_at
    return list(reversed(list(session.scalars(relevant_messages(source.shop_pk, source.customer_uid)
        .where(or_(message_time() < at, and_(message_time() == at, ChatMessage.id < source.id)))
        .order_by(message_time().desc(), ChatMessage.id.desc()).limit(limit)))))


def history_through(session, shop_pk, uid, boundary, limit=20, anchor_ids=()):
    """内部协作读到真实对话边界，包括最新顾客消息之后的客服外发。"""
    from sqlalchemy import or_, and_
    last = session.get(ChatMessage, boundary)
    if not last or last.shop_pk != shop_pk or last.customer_uid != str(uid):
        raise control.ControlConflict('对话边界不存在或范围已变化')
    at = last.msg_time or last.created_at
    before = or_(message_time() < at, and_(message_time() == at, ChatMessage.id <= last.id))
    recent = list(session.scalars(relevant_messages(shop_pk, uid).where(before)
        .order_by(message_time().desc(), ChatMessage.id.desc()).limit(limit)))
    anchors = {i for i in anchor_ids if isinstance(i, int) and i > 0}
    if len(anchors) > 512:
        raise control.ControlConflict('关联历史超出本轮预算，未生成回复')
    # 最早商品卡可在近期窗口之外，但不能越过本轮固定边界取后来商品。
    for field in (ChatMessage.goods_context, ChatMessage.order_context):
        card = session.scalar(relevant_messages(shop_pk, uid).where(before, field.isnot(None))
            .order_by(message_time().desc(), ChatMessage.id.desc()).limit(1))
        if card:
            anchors.add(card.id)
    linked = list(session.scalars(relevant_messages(shop_pk, uid).where(before, ChatMessage.id.in_(anchors)))) if anchors else []
    rows = sorted({r.id:r for r in recent+linked}.values(), key=lambda r:(r.msg_time or r.created_at,r.id))
    if sum(len(r.content or '') for r in rows) > 64000:
        raise control.ControlConflict('关联历史内容超过本轮预算，未生成回复')
    return rows



def row_for(session,shop_pk,customer_uid):
    return session.scalar(select(ConversationControl).where(ConversationControl.shop_pk==shop_pk,
        ConversationControl.customer_uid==str(customer_uid)).with_for_update().execution_options(populate_existing=True))


def read(session,shop_pk,customer_uid):
    row=session.scalar(select(ConversationControl).where(ConversationControl.shop_pk==shop_pk,
        ConversationControl.customer_uid==str(customer_uid)))
    state=control.view(row)
    saved=json.loads(row.draft_json) if row and row.draft_json else None
    if saved and (saved.get('format_version') != 2 or saved['control_revision']!=state['revision'] or
            saved['source_message_id']!=latest_inbound(session,shop_pk,customer_uid) or
            saved['history_boundary_id']!=latest_message(session,shop_pk,customer_uid)):
        saved=None
    supplement=json.loads(row.supplement_text) if row and row.supplement_text else None
    value = human_workspace.data(row)
    active = value and not value.get('closed') and state['mode']=='human'
    text = human_workspace.facts(value) if active else (supplement['text'] if (supplement and supplement['revision']==state['revision'] and state['mode']=='human') else '')
    from common.services import harness_state
    # 读取页面不改变发送状态；业务进度由模型入口/发送回执从原账接续。
    from common.services.context_archive import history_page
    cold=history_page(session,shop_pk,customer_uid,limit=1)
    return {'control':state,'supplement':text,'draft':saved,
        'history_available':bool(cold['entries']),
        'entries': list({e['id']:e for e in [*cold['entries'],*human_workspace.entries(value)]}.values()), 'batch_closed': bool(value and value.get('closed')),
        'requests': value.get('requests', []) if active else [], 'assessed': bool(active and value.get('assessed')),
        'business': harness_state.read(row),
        'curation': human_workspace.curation_view(session, shop_pk, customer_uid)}


def save_draft(session,shop_pk,customer_uid,owner_id,revision,source_id,content,evidence,history_boundary_id=None,operator_note=''):
    shop=session.get(Shop,shop_pk)
    if shop is None or shop.status!=1 or shop.merged_into_shop_pk is not None or shop.owner_user_id!=owner_id:
        raise control.ControlConflict('店铺已停用或归属变化，旧建议未保存')
    state=control.read(session,shop_pk,customer_uid)
    if state['revision']!=revision:
        raise control.ControlConflict('会话状态或人工补充已变化，请重新生成')
    # 生成建议也先占用人工模式，避免自动回复与人工确认并行。
    if state['mode']!='human':
        raise control.ControlConflict('请先进入人工接管再生成建议')
    row=row_for(session,shop_pk,customer_uid)
    boundary=history_boundary_id if history_boundary_id is not None else source_id
    if (row.revision!=revision or latest_inbound(session,shop_pk,customer_uid)!=source_id
            or latest_message(session,shop_pk,customer_uid)!=boundary):
        raise control.ControlConflict('会话新增了客户消息或对客回复，旧建议未保存，请重新生成')
    # 与内部建议共用控制代际，先保存者胜出，跨窗口迟到结果不能覆盖。
    row.revision += 1
    revision = row.revision
    data={'format_version':2,'history_boundary_id':boundary,'content':content,'operator_note':operator_note,'source_message_id':source_id,'control_revision':revision,
          'knowledge':evidence}
    row.draft_json=json.dumps(data,ensure_ascii=False)
    human_workspace.append(session, row, 'draft', content, source_message_id=source_id,
        control_revision=revision, history_boundary_id=boundary)
    if operator_note:
        human_workspace.append(session, row, 'draft_note', operator_note, source_message_id=source_id,
            control_revision=revision, history_boundary_id=boundary)
    session.flush()
    return data


def save_instruction(session, shop_pk, uid, text, revision, actor_id, answer_id=None):
    if not text.strip() or len(text) > 4000:
        raise ValueError('发给AI的内容不能为空且最多4000字')
    control.change(session, shop_pk, uid, 'human', expected_revision=revision,
        actor_id=actor_id, reason='与AI内部协作')
    row = row_for(session, shop_pk, uid)
    if answer_id:
        from common.services.context_archive import latest
        if not latest(session,shop_pk,uid,'answer',answer_id):raise ValueError('所选回答不存在或不属于当前会话')
    human_workspace.append(session, row, 'instruction', text.strip(), actor_id, answer_id=answer_id)
    row.revision += 1
    row.draft_json = None
    session.flush()
    return read(session, shop_pk, uid)


def save_advice(session, shop_pk, uid, owner_id, revision, source_id, boundary, content, requests=None, resolved=None, withdrawn=None):
    # 复用相同的范围/消息边界检查，但内部建议不覆盖对客草稿。
    shop = session.get(Shop, shop_pk)
    row = row_for(session, shop_pk, uid)
    if (not shop or shop.status != 1 or shop.merged_into_shop_pk is not None or shop.owner_user_id != owner_id
        or not row or row.mode != 'human' or row.revision != revision
        or latest_inbound(session, shop_pk, uid) != source_id or latest_message(session, shop_pk, uid) != boundary):
        raise control.ControlConflict('会话或人工补充已变化，请重新询问AI')
    import uuid
    value = human_workspace.ensure_batch(session, row)
    previous = {r['id']: dict(r) for r in value.get('requests', [])}
    requested_ids = [r.get('id') for r in requests or [] if r.get('id')]
    groups=[set(requested_ids),set(resolved or []),set(withdrawn or [])]
    if (any(i not in previous for i in [*requested_ids, *(resolved or []),*(withdrawn or [])])
        or groups[0]&groups[1] or groups[0]&groups[2] or groups[1]&groups[2]):
        raise control.ControlConflict('AI待补事项引用已变化，请重新分析')
    for request_id in resolved or []:
        previous[request_id]['status'] = 'resolved'
    for request_id in withdrawn or []:
        previous[request_id]['status'] = 'withdrawn'
    for request in requests or []:
        request_id = request.get('id') or str(uuid.uuid4())
        previous[request_id] = {'id': request_id, 'question': request['question'], 'status': 'open',
            'source_message_id': source_id}
    if len(previous) > 64:
        raise control.ControlConflict('本轮待补事项已达上限，请先结束人工处理')
    value['requests'] = list(previous.values()); value['assessed'] = True
    row.workspace_json = json.dumps(value, ensure_ascii=False)
    row.revision += 1
    row.draft_json = None
    human_workspace.append(session, row, 'assistant', content, source_message_id=source_id,
        control_revision=row.revision, history_boundary_id=boundary)
    row.attention_count = control.view(row)['attention_count']
    session.flush()
    return {'content': content, 'control_revision': row.revision, 'source_message_id': source_id,
        'history_boundary_id': boundary}
