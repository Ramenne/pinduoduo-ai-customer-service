"""worker入口只连接已有会话状态/收件租约；不直接访问平台或发送。"""
from common.db.session import session_scope
from common.services import harness_state


class SupersededEvent(ValueError):
    pass


class AlreadyAnsweredEvent(SupersededEvent):
    pass


def prepare(pk, uid, revision, platform_message_id=None, query=""):
    with session_scope() as session:
        value = harness_state.context(session, pk, uid, kind='customer', query=query)
        if platform_message_id:
            from common.services.ai_workspace import pending_event
            status, _ = pending_event(session, pk, uid, platform_message_id)
            if status == 'answered':
                raise AlreadyAnsweredEvent('该来话已有客服回复，不重新打开旧问题')
            if status == 'superseded':
                raise SupersededEvent('当前顾客事件已有更新，旧消息只保留历史')
        if value['revision'] != revision:
            raise ValueError('会话控制已变化')
        return value


def apply(pk, uid, owner, snapshot, decision, evidence):
    with session_scope() as session:
        return harness_state.apply(session, pk, uid, owner, snapshot, decision,
            audience='customer', evidence=evidence)


def execution(pk, uid, round_id, state):
    with session_scope() as session:
        harness_state.execution(session, pk, uid, round_id, state)


def failure(pk,uid,owner,key,category='execution'):
    from common.services.harness_experience import capture
    from common.services.message_delivery import current_inbox,assert_lease
    from common.models.shop_models import Shop
    with session_scope() as session:
        shop=session.get(Shop,pk)
        if not shop or shop.owner_user_id!=owner:return
        handle=current_inbox.get()
        if handle:assert_lease(session,handle,pk,uid)
        capture(session,pk,uid,'failure:'+str(handle[0] if handle else key)+':'+key,category,
            '业务处理未完成，原因待核','程序检测到业务决定未完成；未据此补发或修改知识。',
            source={'event':key[:80],'inbox_id':handle[0] if handle else None})
