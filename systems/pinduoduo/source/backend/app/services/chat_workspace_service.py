"""客服工作台：授权范围内的本地历史、平台分页同步和快捷短语。"""
import base64
import json
from common.services import conversation_control
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from sqlalchemy import and_, exists, func, or_, select
from app.core.business_codes import CODE_FORBIDDEN, CODE_NOT_FOUND, CODE_PARAM_ERROR
from app.core.data_scope import ensure_shop_in_scope, load_user_scope, build_owner_condition
from app.services import chat_service, chat_sync_client, connection_notify
from common.db.repository import Repository
from common.models.chat_workspace_models import ChatQuickPhrase, ChatSyncCheckpoint
from common.models.config_models import LlmConfig
from common.models.conversation_control_models import ConversationControl
from common.models.knowledge_models import Product
from common.models.log_models import ChatMessage, Conversation, MessageLog
from common.models.shop_models import Account, Shop
from common.models.user_models import SysUser
from common.schemas.common import error_response, success_response
from common.utils.time_utils import now_beijing_naive, safe_isoformat


def checkpoint_view(checkpoint):
    if not checkpoint:
        return {'checked': False, 'complete': False, 'next_cursor': None, 'last_attempt_at': None, 'last_success_at': None, 'error': ''}
    return {'checked': True, 'complete': checkpoint.complete, 'next_cursor': checkpoint.next_cursor,
            'last_attempt_at': safe_isoformat(checkpoint.last_attempt_at), 'last_success_at': safe_isoformat(checkpoint.last_success_at),
            'error': checkpoint.last_error or ''}


def _shop_conditions(session, user):
    conditions = [Shop.merged_into_shop_pk.is_(None)]
    condition = build_owner_condition(load_user_scope(session, user.id), Shop.owner_user_id)
    if condition is not None:
        conditions.append(condition)
    return conditions


def list_shops(session, user, *, page=1, page_size=20, q='', status=None, ai_enabled=None, connected=None):
    conditions = _shop_conditions(session, user)
    if q:
        conditions.append(or_(Shop.shop_id.contains(q, autoescape=True), Shop.shop_name.contains(q, autoescape=True),
                              Shop.remark.contains(q, autoescape=True), Shop.owner_user_id.in_(select(SysUser.id).where(SysUser.username.contains(q, autoescape=True)))))
    if status is not None:
        conditions.append(Shop.status == status)
    ai_exists = exists(select(LlmConfig.id).where(LlmConfig.shop_pk == Shop.id, LlmConfig.ai_enabled.is_(True)))
    if ai_enabled is not None:
        conditions.append(ai_exists if ai_enabled else ~ai_exists)
    keys = session.execute(select(Shop.id, Shop.shop_id, Shop.owner_user_id).where(*conditions)).all()
    state_map = connection_notify.query_connected_batch([(r.shop_id, r.owner_user_id) for r in keys])
    if connected is not None:
        conditions.append(Shop.id.in_([r.id for r in keys if bool(state_map.get(str(r.shop_id))) == connected]))
    total = session.scalar(select(func.count()).select_from(Shop).where(*conditions)) or 0
    rows = session.scalars(select(Shop).where(*conditions).order_by(Shop.created_at.desc(), Shop.id.desc()).offset((page-1)*page_size).limit(page_size)).all()
    ids = [r.id for r in rows]
    owners = {r.id:r.username for r in session.scalars(select(SysUser).where(SysUser.id.in_([r.owner_user_id for r in rows])))}
    configs = {r.shop_pk:r for r in session.scalars(select(LlmConfig).where(LlmConfig.shop_pk.in_(ids)).order_by(LlmConfig.id))}
    accounts = {r.shop_pk:r for r in session.scalars(select(Account).where(Account.shop_pk.in_(ids)).order_by(Account.updated_at, Account.id))}
    syncs = {r.shop_pk:r for r in session.scalars(select(ChatSyncCheckpoint).where(ChatSyncCheckpoint.shop_pk.in_(ids), ChatSyncCheckpoint.customer_uid == ''))}
    today = now_beijing_naive().replace(hour=0, minute=0, second=0, microsecond=0)
    counts = dict(session.execute(select(MessageLog.shop_pk, func.count()).where(MessageLog.shop_pk.in_(ids), MessageLog.created_at >= today,
                   MessageLog.process_result.in_(['ai_reply','auto_reply','manual_sent','manual'])).group_by(MessageLog.shop_pk)).all())
    catalogs = {}
    for product in session.scalars(select(Product).where(Product.shop_pk.in_(ids), Product.status == 1,
            or_(Product.platform_on_sale.is_(None), Product.platform_on_sale == 1)).order_by(Product.id)):
        catalog = catalogs.setdefault(product.shop_pk, [])
        if len(catalog) < 12:
            catalog.append({'goods_id': product.goods_id, 'goods_name': product.goods_name,
                'price': str(product.price) if product.price is not None else None})
    global_config = session.scalar(select(LlmConfig).where(LlmConfig.shop_pk.is_(None)))
    items=[]
    for shop in rows:
        config, account = configs.get(shop.id, global_config), accounts.get(shop.id)
        from app.services.account_service import serialize_shop
        item = serialize_shop(shop)
        enabled = bool(config and config.ai_enabled)
        ready = bool(config and config.api_key_enc and config.model_name)
        item.update(shop_pk=shop.id, owner=owners.get(shop.owner_user_id, str(shop.owner_user_id or '')),
                    connected=bool(state_map.get(str(shop.shop_id))), ai_enabled=enabled, ai_ready=ready,
                    shop_products=catalogs.get(shop.id, []), model_name=config.model_name if config else '', today_replies=counts.get(shop.id,0),
                    account_state=account.login_state if account else 'unverified', has_cookie=bool(account and account.cookies_enc),
                    sync=checkpoint_view(syncs.get(shop.id)))
        items.append(item)
    return success_response(data={'list':items,'total':total,'page':page,'page_size':page_size})


def _date(value, *, end=False):
    if not value:
        return None
    parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
    if end and len(value)==10: parsed += timedelta(days=1,microseconds=-1)
    return parsed.astimezone(ZoneInfo('Asia/Shanghai')).replace(tzinfo=None) if parsed.tzinfo else parsed


def conversations(session, user, *, shop_pk, page=1, page_size=30, q='', start_time=None, end_time=None):
    denied = ensure_shop_in_scope(session, shop_pk, user.id)
    if denied is not None:
        return denied
    try:
        start,end = _date(start_time),_date(end_time,end=True)
        if start and end and start>end:
            raise ValueError()
    except (ValueError,TypeError):
        return error_response(CODE_PARAM_ERROR,'请检查查询时间范围')
    conditions=[Conversation.shop_pk==shop_pk]
    message_conditions=[ChatMessage.shop_pk==Conversation.shop_pk, ChatMessage.customer_uid==Conversation.customer_uid]
    if start:message_conditions.append(func.coalesce(ChatMessage.msg_time,ChatMessage.created_at)>=start)
    if end:message_conditions.append(func.coalesce(ChatMessage.msg_time,ChatMessage.created_at)<=end)
    if q:
        text_match = ChatMessage.content.contains(q,autoescape=True)
        name_match = or_(Conversation.nickname.contains(q,autoescape=True), Conversation.customer_uid.contains(q,autoescape=True))
        conditions.append(or_(name_match & exists(select(ChatMessage.id).where(*message_conditions)) if start or end else name_match,
                              exists(select(ChatMessage.id).where(*message_conditions,text_match))))
    elif start or end:
        conditions.append(exists(select(ChatMessage.id).where(*message_conditions)))
    preview = select(ChatMessage.content).where(ChatMessage.shop_pk==Conversation.shop_pk,ChatMessage.customer_uid==Conversation.customer_uid).order_by(func.coalesce(ChatMessage.msg_time,ChatMessage.created_at).desc(),ChatMessage.id.desc()).limit(1).correlate(Conversation).scalar_subquery()
    total=session.scalar(select(func.count()).select_from(Conversation).where(*conditions)) or 0
    rows=session.execute(select(Conversation,preview.label('last_content')).where(*conditions).order_by(Conversation.last_msg_at.desc(),Conversation.id.desc()).offset((page-1)*page_size).limit(page_size)).all()
    sync=session.scalar(select(ChatSyncCheckpoint).where(ChatSyncCheckpoint.shop_pk==shop_pk,ChatSyncCheckpoint.customer_uid==''))
    items=[]
    uids = [conv.customer_uid for conv, _ in rows]
    controls = {r.customer_uid: conversation_control.view(r) for r in session.scalars(select(ConversationControl)
        .where(ConversationControl.shop_pk==shop_pk, ConversationControl.customer_uid.in_(uids)))} if uids else {}
    for conv,summary in rows:
        item=chat_service.serialize_conversation(conv)
        item.update(last_content=summary or '',unread=conv.unread_count or 0,
            attention_count=controls.get(conv.customer_uid, {}).get('attention_count', 0))
        items.append(item)
    return success_response(data={'list':items,'total':total,'has_more':page*page_size<total,'page':page,'source':'archive','sync':checkpoint_view(sync)})


def attention(session, user):
    rows = session.execute(select(Shop.id, func.sum(func.coalesce(ConversationControl.attention_count, 1)))
        .join(ConversationControl, ConversationControl.shop_pk==Shop.id)
        .where(*_shop_conditions(session,user), ConversationControl.mode=='human').group_by(Shop.id)).all()
    return success_response(data={'total': sum(int(count) for _, count in rows),
        'shops': [{'shop_pk': pk, 'count': int(count)} for pk,count in rows]})


def _message_view(row):
    result=chat_service.serialize_message(row)
    result.update(msg_id=row.msg_id,msg_at=safe_isoformat(row.msg_time or row.created_at))
    return result


def messages(session,user,*,shop_pk,customer_uid,before=None,after_id=None,limit=50,q='',start_time=None,end_time=None):
    denied=ensure_shop_in_scope(session,shop_pk,user.id)
    if denied is not None:return denied
    timestamp=func.coalesce(ChatMessage.msg_time,ChatMessage.created_at)
    conditions=[ChatMessage.shop_pk==shop_pk,ChatMessage.customer_uid==customer_uid]
    try:
        start,end=_date(start_time),_date(end_time,end=True)
        if start and end and start>end:raise ValueError()
        if before:
            stamp,pk=json.loads(base64.urlsafe_b64decode(before+'='*(-len(before)%4)))
            stamp=datetime.fromisoformat(stamp);pk=int(pk)
            conditions.append(or_(timestamp<stamp,and_(timestamp==stamp,ChatMessage.id<pk)))
    except (ValueError,TypeError,UnicodeError,json.JSONDecodeError):
        return error_response(CODE_PARAM_ERROR,'历史分页位置或时间范围无效，请刷新后重试')
    if after_id is not None:conditions.append(ChatMessage.id>after_id)
    if q:conditions.append(ChatMessage.content.contains(q,autoescape=True))
    if start:conditions.append(timestamp>=start)
    if end:conditions.append(timestamp<=end)
    order=[ChatMessage.id.asc()] if after_id is not None else [timestamp.desc(),ChatMessage.id.desc()]
    rows=list(session.scalars(select(ChatMessage).where(*conditions).order_by(*order).limit(limit+1)))
    more=len(rows)>limit;rows=rows[:limit]
    next_after=max((r.id for r in rows),default=after_id or 0)
    if after_id is None:rows.reverse()
    cursor=None
    if rows:
        cursor=base64.urlsafe_b64encode(json.dumps([(rows[0].msg_time or rows[0].created_at).isoformat(),rows[0].id]).encode()).decode().rstrip('=')
    sync=session.scalar(select(ChatSyncCheckpoint).where(ChatSyncCheckpoint.shop_pk==shop_pk,ChatSyncCheckpoint.customer_uid==customer_uid))
    conv=session.scalar(select(Conversation).where(Conversation.shop_pk==shop_pk,Conversation.customer_uid==customer_uid).order_by(Conversation.id))
    return success_response(data={'messages':[_message_view(r) for r in rows], 'has_more':more,'before_cursor':cursor,
                                 'next_after_id':next_after,'sync':checkpoint_view(sync),'conversation_id':conv.id if conv else None,'source':'archive',
                                 'ai_control':conversation_control.read(session, shop_pk, customer_uid)})


def sync_page(session,user,*,shop_pk,customer_uid='',cursor=None):
    denied=ensure_shop_in_scope(session,shop_pk,user.id)
    if denied is not None:return denied
    shop=session.get(Shop,shop_pk)
    if shop.status!=1:return error_response(CODE_PARAM_ERROR,'店铺已停用，可查询本地历史；请先启用后同步平台')
    if not customer_uid:
        try:page=int(cursor or 1);assert 1<=page<=10000
        except (ValueError,TypeError,AssertionError):return error_response(CODE_PARAM_ERROR,'会话同步位置无效')
        result=chat_sync_client.fetch_conversations(shop.shop_id,shop.owner_user_id,page=page)
        if result.ok:chat_service._persist_synced_conversations(session,shop_pk,result.items)
    else:
        result=chat_sync_client.fetch_history(shop_pk,shop.shop_id,shop.owner_user_id,customer_uid,cursor=cursor,max_pages=1)
    checkpoint=Repository(ChatSyncCheckpoint,session).get_by(shop_pk=shop_pk,customer_uid=customer_uid)
    if checkpoint is None:checkpoint=Repository(ChatSyncCheckpoint,session).create(shop_pk=shop_pk,customer_uid=customer_uid)
    checkpoint.last_attempt_at=now_beijing_naive()
    if not result.ok:
        checkpoint.last_error=result.message or '平台同步失败'
        checkpoint.complete=False
        session.flush()
        return chat_service._platform_error(checkpoint.last_error,'平台同步失败')
    checkpoint.last_success_at=now_beijing_naive()
    checkpoint.complete=bool(result.complete and not result.sync_error)
    checkpoint.next_cursor=str(result.next_cursor) if result.next_cursor is not None else None
    checkpoint.last_error=result.sync_error
    session.flush()
    return success_response(data={**checkpoint_view(checkpoint),'collected':len(result.items),'persisted':result.persisted,
                                  'messages':result.items if customer_uid else [],'source':'platform'})


def read_conversation(session,user,shop_pk,customer_uid):
    denied=ensure_shop_in_scope(session,shop_pk,user.id)
    if denied is not None:return denied
    # 只维护本地未读，不把界面浏览误称为平台已读。
    conv=session.scalar(select(Conversation).where(Conversation.shop_pk==shop_pk,Conversation.customer_uid==customer_uid))
    if conv:conv.unread_count=0
    return success_response(data={'read':True,'source':'archive'})


def phrases(session,user,shop_pk):
    denied=ensure_shop_in_scope(session,shop_pk,user.id)
    if denied is not None:return denied
    rows=session.scalars(select(ChatQuickPhrase).where(ChatQuickPhrase.enabled.is_(True),or_(ChatQuickPhrase.shop_pk==shop_pk,and_(ChatQuickPhrase.shop_pk.is_(None),ChatQuickPhrase.owner_user_id==user.id))).order_by(ChatQuickPhrase.id.desc())).all()
    admin=load_user_scope(session,user.id).is_admin
    return success_response(data={'list':[{'id':r.id,'title':r.title,'content':r.content,'shared':r.shop_pk is not None,'editable':admin or r.owner_user_id==user.id} for r in rows]})


def save_phrase(session,user,*,shop_pk,title,content,shared=False,phrase_id=None,enabled=True):
    denied=ensure_shop_in_scope(session,shop_pk,user.id)
    if denied is not None:return denied
    record=session.get(ChatQuickPhrase,phrase_id) if phrase_id else None
    if phrase_id and (record is None or (record.owner_user_id!=user.id and not load_user_scope(session,user.id).is_admin)):
        return error_response(CODE_NOT_FOUND,'短语不存在或无权修改')
    if record is not None and record.shop_pk is not None and record.shop_pk!=shop_pk:
        return error_response(CODE_FORBIDDEN,'不能跨店铺修改短语')
    if record is None:record=ChatQuickPhrase(owner_user_id=user.id);session.add(record)
    record.shop_pk=shop_pk if shared else None
    record.title=title.strip();record.content=content.strip();record.enabled=enabled
    session.flush()
    return success_response(data={'id':record.id,'saved':True})
