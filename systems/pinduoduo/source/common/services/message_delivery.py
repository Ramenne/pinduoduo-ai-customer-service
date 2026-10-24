"""跨进程收发权威。事务先于外部效果；不确定外发只读核对，绝不自动重放。

收件租约只保护准备工作。外发先持久化dispatching，之后不再给任何owner发送权。
租约回收用token栅栏，HTTP发出后即使进程死亡，也只进入结果核对。
"""
import hashlib
import json
import time
import uuid
from functools import wraps
from contextvars import ContextVar
from sqlalchemy import select, update, func
from sqlalchemy.exc import IntegrityError, OperationalError
from common.db.session import session_scope
from common.models.message_delivery_models import MessageInbox, MessageDelivery, MessagePollState
from common.services import conversation_control
from common.services.send_verdict import from_state, accepted_or_confirmed

current_inbox = ContextVar('oin_current_inbox', default=None)
dispatch_request_id = ContextVar('oin_dispatch_request_id', default=None)
LEASE = 180


class DeliveryPending(ValueError):
    """原OIN SendPending：仍由原效果负责，后来输入等待而不抢发或误接管。"""


class LeaseLost(ValueError):
    """未取得有效收件处理权，尚未授权发送；交给持久收件恢复，不能记已完成。"""


class StaleContext(ValueError):
    """提交前对话已更新，尚未发出；旧输入归档，后续新输入继续处理。"""


def assert_lease(session, handle, shop_pk, uid):
    row=session.scalar(select(MessageInbox).where(MessageInbox.id==handle[0]).with_for_update())
    if (row is None or row.token!=handle[1] or row.state!='processing' or row.due_at<=db_clock(session)
            or row.shop_pk!=shop_pk or row.customer_uid!=str(uid)):
        raise LeaseLost('消息处理权已失效')
    return row


def db_clock(session):
    """沿原OIN ChatStore：租约校验使用数据库时钟，不能信各worker本机时间。"""
    if session.get_bind().dialect.name == 'mysql':
        return float(session.scalar(select(func.unix_timestamp())))
    return float(session.scalar(select((func.julianday('now') - 2440587.5) * 86400)))


def clock_now():
    with session_scope() as s:
        return db_clock(s)


def validate_scope(shop_pk, owner_id, platform_shop_id):
    from common.models.shop_models import Shop
    with session_scope() as s:
        shop=s.get(Shop,shop_pk)
        if (not shop or shop.owner_user_id!=owner_id or shop.status!=1 or shop.merged_into_shop_pk
                or str(shop.shop_id)!=str(platform_shop_id)):
            raise ValueError('店铺归属或启用状态已变化')


def _deadlock_retry(fn):
    """只重试MySQL明确回滚的数据库竞争；不包裹任何外部发送。"""
    @wraps(fn)
    def run(*args, **kwargs):
        for attempt in range(4):
            try:
                return fn(*args, **kwargs)
            except OperationalError as exc:
                code = getattr(exc.orig, 'args', (None,))[0]
                if code not in (1205,1213) or attempt == 3:
                    raise
                time.sleep(.02 * 2 ** attempt)
    return run


def _insert(session, model, where, **values):
    """唯一键处理插入竞态；仅回滚savepoint，不吃掉其它存储错误。"""
    try:
        with session.begin_nested():
            row = model(**values)
            session.add(row)
            session.flush()
        return row
    except IntegrityError:
        row = session.scalar(select(model).where(*where).with_for_update())
        if row is None:
            raise
        return row


@_deadlock_retry
def receive(shop_pk, uid, mid, payload, *, defer=False):
    if not uid or not mid or len(str(uid)) > 128 or len(str(mid)) > 128:
        raise ValueError('缺少有效平台消息标识，不能自动回复')
    body = json.dumps(payload, ensure_ascii=False)
    if len(body.encode()) > 65536:
        raise ValueError('消息超出自动处理上限')
    registration = str(uuid.uuid4())
    with session_scope() as s:
        row = _insert(s, MessageInbox, [MessageInbox.shop_pk == shop_pk,
            MessageInbox.customer_uid == str(uid), MessageInbox.message_id == str(mid)],
            shop_pk=shop_pk, customer_uid=str(uid), message_id=str(mid), payload=body,
            state='pending', due_at=0, token=registration)
        if row.token == registration:
            if defer:
                row.state = 'backfill'
            # 旧版本/手动导入已保存同一平台消息，却没有处理账，不能当新问题自动重放。
            from common.models.log_models import ChatMessage
            known = s.scalar(select(ChatMessage.id).where(ChatMessage.shop_pk == shop_pk,
                ChatMessage.customer_uid == str(uid), ChatMessage.msg_id == str(mid)).limit(1))
            if known:
                row.state, row.result = ('done','archived_history') if defer else ('review','existing_history_requires_review')
                if not defer:
                    conversation_control.change(s, shop_pk, str(uid), 'human', reason='历史消息缺少可靠处理记录，请核对平台后接待')
        return row.id


@_deadlock_retry
def claim(inbox_id, now=None):
    token = str(uuid.uuid4())
    with session_scope() as s:
        now = db_clock(s) if now is None else now
        candidate = s.get(MessageInbox, inbox_id)
        if candidate is None:
            return None
        older = s.scalar(select(MessageInbox.id).where(MessageInbox.shop_pk == candidate.shop_pk,
            MessageInbox.customer_uid == candidate.customer_uid, MessageInbox.id < inbox_id,
            MessageInbox.state.in_(['pending','processing'])).limit(1))
        if older is not None:
            return None  # 跨进程也不越过同客户尚未完成的输入。
        result = s.execute(update(MessageInbox).where(MessageInbox.id == inbox_id,
            MessageInbox.state.in_(['pending', 'processing']), MessageInbox.due_at <= now
        ).values(state='processing', token=token, due_at=now + LEASE,
                 attempts=MessageInbox.attempts + 1))
        if result.rowcount != 1:
            return None
        row = s.get(MessageInbox, inbox_id)
        # 一次外发已登记，崩溃后不再重新生成或降级补发。
        sent = s.scalar(select(MessageDelivery).where(MessageDelivery.inbox_id == row.id).with_for_update())
        if sent is not None:
            row.state = 'done' if accepted_or_confirmed(delivery_view(sent)) else 'review'
            row.result = 'recovered_' + sent.state
            return None
        if row.attempts > 3:
            row.state, row.result = 'review', 'processing_exhausted'
            conversation_control.change(s, row.shop_pk, row.customer_uid, 'human', reason='消息恢复多次失败，请人工接待')
            return None
        return (row.id, token)


def finish(handle, result='done', *, retry=False):
    with session_scope() as s:
        now = db_clock(s)
        s.execute(update(MessageInbox).where(MessageInbox.id == handle[0],
            MessageInbox.token == handle[1], MessageInbox.state == 'processing', MessageInbox.due_at > now).values(
                state='pending' if retry else 'done', result=result,
                due_at=now + 15 if retry else 0, token=None,
                **({'attempts':MessageInbox.attempts-1} if result == 'send_pending' else {})))


def recoverable(shop_pk, now=None, limit=30):
    with session_scope() as s:
        return [(r.id, json.loads(r.payload)) for r in s.scalars(select(MessageInbox).where(
            MessageInbox.shop_pk == shop_pk, MessageInbox.state.in_(['pending','processing']),
            MessageInbox.due_at <= (db_clock(s) if now is None else now)
        ).order_by(MessageInbox.id).limit(limit))]


@_deadlock_retry
def reserve(shop_pk, uid, operation_key, kind, body, *, revision=None, handle=None, owner_id=None, platform_shop_id=None, baseline=None, expected_mode='auto', history_boundary_id=None):
    """唯一外发权。返回(existing,new)，不把已有不确定记录授权为新发送。"""
    if not operation_key or len(operation_key) > 128:
        raise ValueError('无效发送操作标识')
    if not uid or len(str(uid)) > 128 or len(json.dumps(body, ensure_ascii=False).encode()) > 65536:
        raise ValueError('发送内容或客户标识超出处理范围')
    digest = hashlib.sha256(json.dumps([str(uid), kind, body], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    with session_scope() as s:
        now = db_clock(s)
        if owner_id is not None:
            from common.models.shop_models import Shop
            shop = s.scalar(select(Shop).where(Shop.id==shop_pk).with_for_update())
            if not shop or shop.owner_user_id != owner_id or shop.status != 1 or shop.merged_into_shop_pk or (platform_shop_id is not None and str(shop.shop_id) != str(platform_shop_id)):
                raise ValueError('店铺归属或启用状态已变化')
        existing = s.scalar(select(MessageDelivery).where(
            MessageDelivery.shop_pk == shop_pk, MessageDelivery.operation_key == operation_key))
        if existing is not None:
            if existing.body_hash != digest or existing.customer_uid != str(uid):
                raise ValueError('同一发送标识的内容已变化，请先核对原发送')
            return delivery_view(existing), False
        if revision is not None:
            # 与原OIN完整CAS相同：会话代际/人工归属与收件租约在同一提交边界检查。
            from common.models.conversation_control_models import ConversationControl
            _insert(s,ConversationControl,[ConversationControl.shop_pk==shop_pk,
                ConversationControl.customer_uid==str(uid)],shop_pk=shop_pk,
                customer_uid=str(uid),mode='auto',revision=0)
            locked_control = s.scalar(select(ConversationControl).where(ConversationControl.shop_pk==shop_pk,
                ConversationControl.customer_uid==str(uid)).with_for_update())
            # 原OIN唯一command_id语义；等待会话锁之后必须做当前读，不能使用
            # MySQL REPEATABLE READ在等待前建立的旧快照误判为另一个新发送。
            concurrent = s.scalar(select(MessageDelivery).where(MessageDelivery.shop_pk==shop_pk,
                MessageDelivery.operation_key==operation_key).with_for_update())
            if concurrent is not None:
                if concurrent.body_hash!=digest or concurrent.customer_uid!=str(uid):
                    raise ValueError('发送标识冲突')
                return delivery_view(concurrent),False
        if handle:
            # 行锁与回收同一行互斥；租约过期的旧生成不能发送。
            inbox = s.scalar(select(MessageInbox).where(MessageInbox.id == handle[0]).with_for_update())
            if (inbox is None or inbox.token != handle[1] or inbox.state != 'processing'
                    or inbox.due_at <= now or inbox.shop_pk != shop_pk or inbox.customer_uid != str(uid)):
                raise LeaseLost('消息处理权已过期')
        if revision is not None:
            control = conversation_control.view(locked_control)
            if control['mode'] != expected_mode or control['revision'] != revision:
                raise ValueError('会话已接管或代际改变')
            if history_boundary_id is not None:
                from common.services.ai_workspace import latest_message
                if latest_message(s,shop_pk,uid,lock=True)!=history_boundary_id:
                    raise StaleContext('顾客对话已变化，旧草稿未发送，请重新确认')
            unresolved = s.scalar(select(MessageDelivery.id).where(MessageDelivery.shop_pk == shop_pk,
                MessageDelivery.customer_uid == str(uid),
                MessageDelivery.state.in_(['dispatching','uncertain','review'])).limit(1).with_for_update())
            if unresolved is not None and expected_mode=='auto':
                raise DeliveryPending('此会话仍有未核对发送，等待原发送结果')
        request_id = str(10**12 + uuid.uuid4().int % (9 * 10**12))
        row = _insert(s, MessageDelivery, [MessageDelivery.shop_pk == shop_pk,
            MessageDelivery.operation_key == operation_key], shop_pk=shop_pk,
            customer_uid=str(uid), operation_key=operation_key, kind=kind, body_hash=digest,
            body=json.dumps(body, ensure_ascii=False, sort_keys=True),
            inbox_id=handle[0] if handle else None, state='dispatching',
            baseline_ids=json.dumps(baseline['ids']) if baseline else None,
            baseline_at=baseline['at'] if baseline else None,
            request_id=request_id, due_at=now+30)
        if row.body_hash != digest:
            raise ValueError('发送标识冲突')
        return delivery_view(row), row.request_id == request_id


def delivery_view(row):
    return {'verdict':from_state(row.state).value, **{key: getattr(row, key) for key in ('id','shop_pk','customer_uid','operation_key',
        'kind','body','state','platform_id','request_id','checks','reason','baseline_at')}}


def find_delivery(shop_pk, operation_key):
    with session_scope() as s:
        row=s.scalar(select(MessageDelivery).where(MessageDelivery.shop_pk==shop_pk,
            MessageDelivery.operation_key==operation_key))
        return delivery_view(row) if row else None


def waiting_for_receipt(shop_pk, uid):
    with session_scope() as s:
        return s.scalar(select(MessageDelivery.id).where(MessageDelivery.shop_pk==shop_pk,
            MessageDelivery.customer_uid==str(uid),MessageDelivery.state=='dispatching').limit(1)) is not None


def settle(delivery_id, result, *, error=None):
    """API明确接受与平台历史确认分开；异常不确定，不猜测未发送。"""
    with session_scope() as s:
        row = s.scalar(select(MessageDelivery).where(MessageDelivery.id == delivery_id).with_for_update())
        if row is None:
            raise ValueError('发送登记不存在')
        if row.state in ('confirmed', 'not_sent', 'review'):
            return delivery_view(row)  # 迟到响应不能覆盖核对结论。
        inner = (result or {}).get('result') or {}
        if isinstance(inner, dict):
            mid = inner.get('msg_id') or inner.get('message_id')
            if mid:
                row.platform_id = str(mid)[:128]
        accepted = bool(result and result.get('success') is True)
        row.state = ('confirmed' if row.kind == 'transfer' else 'accepted') if accepted else 'uncertain'
        row.reason = None if accepted else '发送结果不明，请核对平台记录'
        row.due_at = db_clock(s) + 5
        if not accepted:
            conversation_control.change(s, row.shop_pk, row.customer_uid, 'human', reason=row.reason)
        return delivery_view(row)


def pending_checks(shop_pk, now=None):
    with session_scope() as s:
        return [delivery_view(r) for r in s.scalars(select(MessageDelivery).where(
            MessageDelivery.shop_pk == shop_pk,
            ((MessageDelivery.state.in_(['dispatching','uncertain'])) |
             ((MessageDelivery.state == 'accepted') & (MessageDelivery.checks == 0))),
            MessageDelivery.due_at <= (db_clock(s) if now is None else now)
        ).order_by(MessageDelivery.checks, MessageDelivery.id).limit(20))]


def claim_check(delivery_id):
    with session_scope() as s:
        result = s.execute(update(MessageDelivery).where(MessageDelivery.id == delivery_id,
            MessageDelivery.state.in_(['dispatching','accepted','uncertain']),
            MessageDelivery.due_at <= db_clock(s)).values(due_at=db_clock(s)+60))
        return result.rowcount == 1


def receipt_deadline(delivery_id):
    """本地等待超过五分钟仍无强关联回执，交人工；不因此查询或重发。"""
    from datetime import timedelta
    from common.utils.time_utils import now_beijing_naive
    with session_scope() as s:
        row = s.scalar(select(MessageDelivery).where(MessageDelivery.id == delivery_id).with_for_update())
        if (row is None or row.state not in ('dispatching','uncertain')
                or row.created_at is None or row.created_at > now_beijing_naive() - timedelta(minutes=5)):
            return False
        row.state, row.reason = 'review', '尚无可关联的发送回执，请人工核对；不会自动重发'
        conversation_control.change(s,row.shop_pk,row.customer_uid,'human',reason=row.reason)
        return True


def check_result(delivery_id, messages, *, readable=True):
    """只接受相同平台ID/回显request_id且同客户的出站事实，绝不按相同文案猜测。"""
    with session_scope() as s:
        row = s.scalar(select(MessageDelivery).where(MessageDelivery.id == delivery_id).with_for_update())
        if row is None or row.state in ('confirmed','not_sent','review'):
            return
        matched = None
        for msg in messages:
            to = msg.get('to') or {}
            mid = msg.get('msg_id')
            if ((msg.get('from') or {}).get('role') == 'mall_cs'
                    and str(to.get('uid')) == row.customer_uid
                    and ((row.platform_id and str(mid) == row.platform_id)
                         or (not row.platform_id and msg.get('request_id') is not None and str(msg['request_id']) == row.request_id))):
                matched = msg
                break
        if matched is None and readable and not row.platform_id and row.baseline_ids is not None and row.kind in ('text','image'):
            # 原OIN SendTransaction/StudioForm的精确回读：提交前基线之外的新ID，
            # 同目标/方向/类型/正文hash/时间窗，且只有一个候选。绝非仅凭同文案。
            baseline=set(json.loads(row.baseline_ids))
            candidates={}
            for msg in messages:
                mid=msg.get('msg_id')
                try:
                    ts=float(msg.get('ts') or 0)
                    if ts>100000000000:ts/=1000
                except (TypeError,ValueError):continue
                if (not mid or str(mid) in baseline or ts<int(row.baseline_at)
                        or (msg.get('from') or {}).get('role')!='mall_cs'
                        or str((msg.get('to') or {}).get('uid'))!=row.customer_uid
                        or msg.get('type')!=(0 if row.kind=='text' else 1)):
                    continue
                digest=hashlib.sha256(json.dumps([row.customer_uid,row.kind,msg.get('content')],ensure_ascii=False,sort_keys=True).encode()).hexdigest()
                if digest==row.body_hash:candidates[str(mid)]=msg
            if len(candidates)>1:
                row.state,row.reason='review','平台回读出现多个匹配候选，请人工核对'
                conversation_control.change(s,row.shop_pk,row.customer_uid,'human',reason=row.reason)
                return
            if len(candidates)==1:matched=next(iter(candidates.values()))
        row.checks += 1
        if matched:
            row.state, row.reason = 'confirmed', '平台消息记录已核对'
            if matched.get('msg_id'):
                row.platform_id = str(matched['msg_id'])[:128]
        elif row.checks >= 5:
            row.state, row.reason = 'review', '未取得可关联平台回执，请人工核对；不能据此判定未发'
            conversation_control.change(s, row.shop_pk, row.customer_uid, 'human', reason=row.reason)
        else:
            row.reason = '平台记录暂不可读，等待核对' if not readable else '尚未取得可唯一关联的记录，等待核对'
            if row.state == 'dispatching':
                row.state = 'uncertain'
                conversation_control.change(s, row.shop_pk, row.customer_uid, 'human', reason='发送中断，请核对平台记录')
            row.due_at = db_clock(s) + min(30, 5 * 2 ** row.checks)


@_deadlock_retry
def poll_position(shop_pk, initial, *, enabled_since=0):
    with session_scope() as s:
        row = _insert(s, MessagePollState, [MessagePollState.shop_pk == shop_pk],
            shop_pk=shop_pk, since=initial, checked_at=initial)
        return row.since  # 普通配置保存时间不能跳过停机期间的新消息。


def reset_poll(session, shop_pk, when):
    row = _insert(session, MessagePollState, [MessagePollState.shop_pk == shop_pk],
                  shop_pk=shop_pk, since=when, checked_at=when)
    row.since = max(row.since, when)


def advance_poll(shop_pk, position):
    with session_scope() as s:
        # 只在消息已持久收下且所有分页完整后推进；并发扫描不能倒退。
        s.execute(update(MessagePollState).where(MessagePollState.shop_pk == shop_pk,
            MessagePollState.since < position).values(since=position, checked_at=time.time()))


def resolve(session, shop_pk, uid, delivery_id, outcome, *, actor_id, note):
    if outcome not in ('confirmed','not_sent') or not note or len(note.strip()) < 4:
        raise ValueError('请先在平台核对，并填写核对依据')
    row = session.scalar(select(MessageDelivery).where(MessageDelivery.id == delivery_id,
        MessageDelivery.shop_pk == shop_pk, MessageDelivery.customer_uid == str(uid)).with_for_update())
    if row is None or row.state not in ('uncertain','review','dispatching','accepted'):
        raise ValueError('发送状态已变化，请刷新')
    if row.state == 'dispatching' and row.due_at > db_clock(session):
        raise ValueError('发送仍在进行，请等待结果')
    row.state, row.resolved_by = outcome, actor_id
    row.resolution_note = note.strip()[:500]
    row.reason = '人工已核对平台记录'
    # 只结束不明效果，不重发原请求、不自动解除人工接管。
    return delivery_view(row)
