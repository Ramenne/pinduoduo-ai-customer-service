"""接管单一权威：持久归属、代际检查；异常由调用方停止自动发送，不回退成auto。"""
import json
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from common.db.session import session_scope
from common.models.conversation_control_models import ConversationControl
from common.utils.time_utils import safe_isoformat


class ControlConflict(ValueError):
    pass


def view(row):
    if row is None:
        return {'mode': 'auto', 'revision': 0, 'reason': None, 'updated_at': None, 'attention_count': 0}
    workspace = json.loads(row.workspace_json) if row.workspace_json else {}
    pending = sum(r.get('status') == 'open' for r in workspace.get('requests', []))
    attention = (pending or (0 if workspace.get('assessed') else 1)) if row.mode == 'human' else 0
    return {'mode': row.mode, 'revision': row.revision, 'reason': row.reason,
            'updated_at': safe_isoformat(row.updated_at), 'attention_count': attention}


def read(session, shop_pk, customer_uid):
    return view(session.scalar(select(ConversationControl).where(
        ConversationControl.shop_pk == shop_pk, ConversationControl.customer_uid == str(customer_uid))))


def change(session, shop_pk, customer_uid, mode, *, expected_revision=None, actor_id=None,
           reason=''):
    if mode not in ('auto', 'human') or not customer_uid:
        raise ValueError('无效的会话控制参数')
    row = session.scalar(select(ConversationControl).where(
        ConversationControl.shop_pk == shop_pk,
        ConversationControl.customer_uid == str(customer_uid)).with_for_update().execution_options(populate_existing=True))
    if row is None:
        if expected_revision not in (None, 0):
            raise ControlConflict('会话状态已变化，请刷新后重试')
        try:
            with session.begin_nested():
                row = ConversationControl(shop_pk=shop_pk, customer_uid=str(customer_uid),
                                          mode=mode, reason=reason[:128], created_by=actor_id)
                session.add(row)
                session.flush()
                if mode == 'human':
                    from common.services.human_workspace import ensure_batch
                    ensure_batch(session, row)
                row.attention_count = 1 if mode == 'human' else 0
            return view(row)
        except IntegrityError:
            row = session.scalar(select(ConversationControl).where(
                ConversationControl.shop_pk == shop_pk,
                ConversationControl.customer_uid == str(customer_uid)).with_for_update().execution_options(populate_existing=True))
    if expected_revision is not None and row.revision != expected_revision:
        raise ControlConflict('会话状态已变化，请刷新后重试')
    if mode == 'auto':
        from common.models.message_delivery_models import MessageDelivery
        pending = session.scalar(select(MessageDelivery.id).where(MessageDelivery.shop_pk == shop_pk,
            MessageDelivery.customer_uid == str(customer_uid),
            MessageDelivery.state.in_(['dispatching','uncertain','review'])).limit(1))
        if pending is not None:
            raise ControlConflict('仍有发送结果待核对，请核对后恢复自动处理')
    if row.mode != mode:
        from common.services import human_workspace
        from common.services.media_handoff import reconcile_manual
        reconcile_manual(session,row)
        if row.mode == 'human' and mode == 'auto':
            human_workspace.finish(session, row)
        row.mode = mode
        row.revision += 1
        row.reason = reason[:128]
        row.created_by = actor_id
        if mode == 'human':
            row.supplement_text = None
            human_workspace.ensure_batch(session, row)
        row.attention_count = view(row)['attention_count']
        session.flush()
    return view(row)


def read_committed(shop_pk, customer_uid):
    with session_scope() as session:
        return read(session, shop_pk, customer_uid)


def take_human(shop_pk, customer_uid, reason='转人工', expected_revision=None):
    with session_scope() as session:
        from common.services.message_delivery import current_inbox,assert_lease
        handle=current_inbox.get()
        if handle:
            assert_lease(session,handle,shop_pk,customer_uid)
        return change(session, shop_pk, customer_uid, 'human', reason=reason,
                      expected_revision=expected_revision)


def supplement(session, shop_pk, customer_uid, text, *, expected_revision, actor_id):
    """人工补充先持久化并占用会话；不向顾客发送，也不审批/发布知识。"""
    if not text.strip() or len(text)>4000:
        raise ValueError('补充内容不能为空且最多4000字')
    state=change(session,shop_pk,customer_uid,'human',expected_revision=expected_revision,
                 actor_id=actor_id,reason='人工补充信息，待确认回复')
    row=session.scalar(select(ConversationControl).where(ConversationControl.shop_pk==shop_pk,
        ConversationControl.customer_uid==str(customer_uid)).with_for_update().execution_options(populate_existing=True))
    from common.services import human_workspace
    value = human_workspace.ensure_batch(session, row)
    human_workspace.append(session, row, 'fact', text.strip(), actor_id)
    # 包括“保持人工模式”时修改补充，也必须使在途建议失效。
    row.revision+=1
    row.supplement_text=json.dumps({'text':text.strip(),'revision':row.revision},ensure_ascii=False)
    row.draft_json=None
    row.created_by=actor_id
    session.flush()
    return view(row)
