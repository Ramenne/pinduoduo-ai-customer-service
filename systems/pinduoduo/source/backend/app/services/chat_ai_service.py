"""工作台 AI 控制：仅更新开关；建议生成与消息发送分离。"""
from sqlalchemy import select
from app.core.data_scope import ensure_shop_in_scope
from common.models.config_models import LlmConfig
from common.models.shop_models import Shop
from common.schemas.common import error_response, success_response
from common.services import service_client


def set_mode(session, user, shop_pk, enabled):
    denied = ensure_shop_in_scope(session, shop_pk, user.id)
    if denied is not None:
        return denied
    shop = session.get(Shop, shop_pk)
    if enabled and shop.status != 1:
        return error_response(40000, '请先启用店铺')
    config = session.scalar(select(LlmConfig).where(LlmConfig.shop_pk == shop_pk))
    if config is None:
        global_config = session.scalar(select(LlmConfig).where(LlmConfig.shop_pk.is_(None)))
        if global_config is None:
            return error_response(40000, '请先在店铺设置中配置 AI 模型和密钥')
        config = LlmConfig(shop_pk=shop_pk, **{key: getattr(global_config, key) for key in (
            'provider_type', 'model_name', 'api_key_enc', 'api_base', 'instructions')})
        session.add(config)
    if enabled and (not config.api_key_enc or not config.model_name):
        return error_response(40000, 'AI 模型或密钥未配置，请先完成店铺 AI 设置')
    was_enabled = bool(config.ai_enabled)
    config.ai_enabled = enabled
    if enabled and not was_enabled:
        import time
        from common.services.message_delivery import reset_poll
        reset_poll(session, shop_pk, time.time())
    session.flush()
    return success_response(data={'ai_enabled': enabled})


def draft(session, user, shop_pk, customer_uid, purpose='draft',instruction_id=None):
    denied = ensure_shop_in_scope(session, shop_pk, user.id)
    if denied is not None:
        return denied
    if instruction_id and purpose=='advice':
        from app.services.chat_instruction_service import execute
        executed=execute(session,user,shop_pk,customer_uid,instruction_id)
        if executed is not None:return executed
    shop = session.get(Shop, shop_pk)
    from common.services import ai_workspace
    source = ai_workspace.latest_message_row(session, shop_pk, customer_uid, inbound=True)
    if source is None:
        return error_response(40000, '当前会话没有可供 AI 回复的客户消息')
    from common.services import conversation_control as control, ai_workspace
    history_boundary=ai_workspace.latest_message(session,shop_pk,customer_uid)
    state=control.read(session,shop_pk,customer_uid)
    try:
        state=control.change(session,shop_pk,customer_uid,'human',expected_revision=state['revision'],
            actor_id=user.id,reason='人工确认AI建议')
        session.commit()  # 接管先于模型调用，消息服务必须能读到同一权威。
    except control.ControlConflict as exc:
        return error_response(40900,str(exc))
    result = service_client.post_json(service_client.websocket_base_url(), '/api/v1/ai/draft',
        {'shop_pk': shop_pk, 'owner_user_id': shop.owner_user_id,
         'customer_uid': customer_uid, 'source_message_id': source.id, 'control_revision':state['revision'], 'history_boundary_id':history_boundary,
         'purpose': purpose, 'operator_source_id': instruction_id}, timeout=85)
    if not result.ok or not result.success:
        return error_response(40000, result.message or 'AI 建议生成失败，请稍后重试')
    # 模型保存可能推进控制代际；推送由外层路由在同一backend进程广播。
    return success_response(data=result.data)


def conversation_control(session, user, shop_pk, customer_uid, mode=None, expected_revision=None):
    from common.services import conversation_control as control
    from common.models.log_models import Conversation
    denied = ensure_shop_in_scope(session, shop_pk, user.id)
    if denied is not None:
        return denied
    exists = session.scalar(select(Conversation.id).where(
        Conversation.shop_pk == shop_pk, Conversation.customer_uid == customer_uid).limit(1))
    if exists is None:
        return error_response(40400, '客户会话不存在，请先同步会话')
    if mode is None:
        return success_response(data=control.read(session, shop_pk, customer_uid))
    if mode not in ('auto', 'human'):
        return error_response(40000, '会话模式无效')
    try:
        state = control.change(session, shop_pk, customer_uid, mode,
                               expected_revision=expected_revision, actor_id=user.id,
                               reason='人工接管' if mode == 'human' else '人工明确恢复自动处理')
    except control.ControlConflict as exc:
        return error_response(40900, str(exc))
    return success_response(data=state)


def ai_workspace(session,user,shop_pk,customer_uid,text=None,expected_revision=None,kind='fact',answer_id=None):
    from common.services import ai_workspace as workspace, conversation_control as control
    from common.models.log_models import Conversation
    denied=ensure_shop_in_scope(session,shop_pk,user.id)
    if denied is not None:return denied
    if session.scalar(select(Conversation.id).where(Conversation.shop_pk==shop_pk,
            Conversation.customer_uid==customer_uid).limit(1)) is None:
        return error_response(40400,'客户会话不存在')
    try:
        if text is not None:
            with session.begin_nested():
                if kind == 'instruction':
                    workspace.save_instruction(session,shop_pk,customer_uid,text,expected_revision,user.id,answer_id=answer_id)
                else:
                    control.supplement(session,shop_pk,customer_uid,text,expected_revision=expected_revision,actor_id=user.id)
        return success_response(data=workspace.read(session,shop_pk,customer_uid))
    except control.ControlConflict as exc:return error_response(40900,str(exc))
    except ValueError as exc:return error_response(40000,str(exc))
