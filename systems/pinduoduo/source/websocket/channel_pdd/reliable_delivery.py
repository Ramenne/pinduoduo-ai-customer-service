"""平台发送适配：登记→单次POST→结果保存；恢复只读平台历史。"""
import time
from common.services import message_delivery as ledger
from common.services.message_delivery import dispatch_request_id


class PreparationUnavailable(RuntimeError):
    """提交前读取失败；没有调用POST，收件可以安全接续准备。"""


def read_baseline(shop_id, user_id, uid):
    from channel_pdd.api.get_chat_history import GetChatHistory
    at=ledger.clock_now()
    ids,cursor,seen=set(),None,set()
    fetcher=GetChatHistory(shop_id,user_id)
    for _ in range(3):
        page=fetcher.fetch_page(uid,start_msg_id=cursor,size=50,recovery=True)
        if not page.get('success'):
            if page.get('auth_required'):
                from channel_pdd.session_health import maintain_session
                maintain_session(shop_id,user_id,renew=False,rejected=True)
            raise PreparationUnavailable('提交前平台记录暂不可读，尚未发送')
        rows=page.get('messages',[])
        ids.update(str(r['msg_id']) for r in rows if r.get('msg_id'))
        def moment(row):
            try:
                value=float(row.get('ts') or 0)
                return value/1000 if value>100000000000 else value
            except (TypeError,ValueError):return 0
        if not page.get('has_more') or (rows and any(0<moment(r)<int(at) for r in rows)):
            return {'ids':sorted(ids),'at':at}
        cursor=rows[-1].get('msg_id') if rows else None
        if not cursor or cursor in seen:break
        seen.add(cursor)
    raise PreparationUnavailable('提交前记录范围不完整，尚未发送')


def dispatch(shop_pk, uid, key, kind, body, callback, *, revision=None, handle=None, owner_id=None, platform_shop_id=None, expected_mode='auto', history_boundary_id=None):
    existing=ledger.find_delivery(shop_pk,key)
    baseline=None
    if existing is None and owner_id is not None and platform_shop_id is not None and kind in ('text','image'):
        ledger.validate_scope(shop_pk,owner_id,platform_shop_id)
        baseline=read_baseline(platform_shop_id,owner_id,uid)
    row, fresh = ledger.reserve(shop_pk, uid, key, kind, body, revision=revision, handle=handle,
        owner_id=owner_id, platform_shop_id=platform_shop_id,baseline=baseline,expected_mode=expected_mode,history_boundary_id=history_boundary_id)
    if not fresh:
        return row
    token = dispatch_request_id.set(row['request_id'])
    try:
        try:
            response = callback()
        except Exception:
            response = None
        return ledger.settle(row['id'], response)
    finally:
        dispatch_request_id.reset(token)


def reconcile(shop_pk, shop_id, user_id):
    from channel_pdd.api.get_chat_history import GetChatHistory
    # 不维持第二个消费者；现有店铺恢复循环唤醒，且只查询已有待核对效果。
    rows = ledger.pending_checks(shop_pk)
    affected = set()
    batch_deadline = time.monotonic() + 25
    for row in rows:
        if time.monotonic() >= batch_deadline:
            break
        if confirm_archived(row):
            affected.add(row['customer_uid'])
            continue
        if row['state'] == 'accepted':
            # 平台已明确接受就是成功；缺少额外送达回显不再触发HTTP或阻塞下一条。
            from sqlalchemy import update
            from common.db.session import session_scope
            from common.models.message_delivery_models import MessageDelivery
            with session_scope() as session:
                session.execute(update(MessageDelivery).where(MessageDelivery.id==row['id'],
                    MessageDelivery.state=='accepted',MessageDelivery.checks==0).values(checks=1))
            continue
        if ledger.receipt_deadline(row['id']):
            affected.add(row['customer_uid'])
            continue
        # 一次发送最多一次事件触发的HTTP核对，之后等推送/人工，不定时反复拉历史。
        if row['checks'] > 0 or not ledger.claim_check(row['id']):
            continue
        messages, cursor, seen = [], None, set()
        readable = True
        deadline = min(batch_deadline, time.monotonic() + 20)
        for _ in range(1):
            if time.monotonic() >= deadline:
                readable = False
                break
            page = GetChatHistory(shop_id, user_id).fetch_page(row['customer_uid'], start_msg_id=cursor, size=50, recovery=True)
            if not page.get('success'):
                if page.get('auth_required'):
                    ledger.check_result(row['id'], [], readable=False)
                    from channel_pdd.session_health import maintain_session
                    maintain_session(shop_id,user_id,renew=False,rejected=True)
                    return affected
                readable = False
                break
            batch = page.get('messages', [])
            messages.extend(batch)
            if page.get('has_more'):
                from channel_pdd.live_message_poll import timestamp
                # 一页未覆盖提交基线时，不用文案匹配猜送达；平台ID/请求ID仍可核对。
                readable = bool(row.get('baseline_at') and batch and
                    min(timestamp(m.get('ts')) for m in batch) < int(row['baseline_at']))
            if not page.get('has_more') or not batch:
                break
            cursor = batch[-1].get('msg_id')
            if not cursor or cursor in seen:
                break
            seen.add(cursor)
        ledger.check_result(row['id'], messages, readable=readable)
        affected.add(row['customer_uid'])
    return affected


def confirm_archived(row):
    """平台ID已在推送档案中时只读本地确认，不按相同文案猜测。"""
    if not row.get('platform_id'):
        return False
    from sqlalchemy import select
    from common.db.session import session_scope
    from common.models.log_models import ChatMessage
    with session_scope() as s:
        msg = s.scalar(select(ChatMessage).where(ChatMessage.shop_pk == row['shop_pk'],
            ChatMessage.customer_uid == row['customer_uid'], ChatMessage.direction == 'out',
            ChatMessage.msg_id == row['platform_id']))
        if msg is None:
            return False
    ledger.check_result(row['id'],[{'msg_id':row['platform_id'],
        'from':{'role':'mall_cs'},'to':{'uid':row['customer_uid']}}])
    return True


def observe_push(shop_pk, raw):
    """只核对平台消息ID/请求ID，收到其它端同文案不会冒充本次发送成功。"""
    import json
    from sqlalchemy import select, or_
    from common.db.session import session_scope
    from common.models.message_delivery_models import MessageDelivery
    if isinstance(raw,(str,bytes)):
        try:raw=json.loads(raw)
        except (ValueError,TypeError):return False
    if not isinstance(raw,dict):return False
    message=raw.get('message')
    if not isinstance(message,dict) or (message.get('from') or {}).get('role')!='mall_cs':return False
    uid=(message.get('to') or {}).get('uid');mid=message.get('msg_id')
    request=message.get('request_id') or raw.get('request_id')
    if not uid or not mid:return False
    matches=[MessageDelivery.platform_id==str(mid)]
    if request is not None:matches.append(MessageDelivery.request_id==str(request))
    with session_scope() as s:
        rows=list(s.scalars(select(MessageDelivery).where(MessageDelivery.shop_pk==shop_pk,
            MessageDelivery.customer_uid==str(uid),MessageDelivery.state.in_(['dispatching','accepted','uncertain']),or_(*matches))))
        ids=[r.id for r in rows]
    if request is not None:message={**message,'request_id':request}
    for ident in ids:ledger.check_result(ident,[message])
    return bool(ids)
