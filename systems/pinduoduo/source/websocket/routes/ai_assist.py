"""仅生成 AI 建议，不调用消息发送器，不写出站消息。"""
import asyncio
import logging
from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import select
from agent.agent_config import AgentConfig
from agent.ai_reply_engine import generate_reply
from agent.round_knowledge import load_round_knowledge, KnowledgeUnavailable
from agent.assist_contracts import parse_advice, parse_draft
from common.services import ai_workspace, conversation_control, harness_state
from channel_pdd.pdd_message import Context, ContextType
from engine.message_consumer import MessageConsumer
from common.db.session import session_scope
from common.models.config_models import LlmConfig
from common.models.shop_models import Shop
from common.models.log_models import ChatMessage
from common.schemas.common import error_response, success_response
import json
from typing import Literal

router = APIRouter(tags=['AI 建议'])
_slots = asyncio.Semaphore(2)
logger = logging.getLogger(__name__)

class DraftRequest(BaseModel):
    shop_pk: int
    owner_user_id: int
    customer_uid: str = Field(..., min_length=1, max_length=128)
    source_message_id: int
    control_revision: int = Field(..., ge=1)
    history_boundary_id: int = Field(..., ge=1)
    operator_source_id: str | None = Field(None, max_length=36)
    purpose: Literal['draft','advice'] = 'draft'


def prepare(payload):
    with session_scope() as session:
        shop = session.get(Shop, payload.shop_pk)
        if shop is None or shop.owner_user_id != payload.owner_user_id or shop.merged_into_shop_pk or shop.status!=1:
            raise ValueError('店铺不存在或无权访问')
        source = session.get(ChatMessage, payload.source_message_id)
        if source is None or source.shop_pk != shop.id or source.customer_uid != payload.customer_uid or source.direction != 'in' or source.msg_type in ('system_status', 'auth', 'mall_system_msg'):
            raise ValueError('客户消息不存在或不属于当前会话')
        config = session.scalar(select(LlmConfig).where(LlmConfig.shop_pk == shop.id))
        if config is None:
            config = session.scalar(select(LlmConfig).where(LlmConfig.shop_pk.is_(None)))
        if config is None:
            raise ValueError('请先配置 AI 模型和密钥')
        control = conversation_control.read(session, shop.id, payload.customer_uid)
        if control['mode']!='human' or control['revision']!=payload.control_revision:
            raise ValueError('会话接管状态已变化，请重新生成建议')
        if (ai_workspace.latest_inbound(session,shop.id,payload.customer_uid)!=payload.source_message_id or
            ai_workspace.latest_message(session,shop.id,payload.customer_uid)!=payload.history_boundary_id):
            raise ValueError('会话新增了客户消息或对客回复，请重新生成建议')
        workspace = ai_workspace.read(session,shop.id,payload.customer_uid)
        agent = AgentConfig.from_llm_config(config)
        # 辅助回复可在托管关闭时使用，不修改持久化开关。
        agent.ai_enabled = True
        if not agent.api_key:
            raise ValueError('AI 密钥缺失或无法解密，请检查店铺设置')
        def context(value):
            try: return json.loads(value) if value else None
            except (TypeError, ValueError): return None
        ctx = Context(type=ContextType.TEXT, content=source.content,
            goods_context=context(source.goods_context), order_context=context(source.order_context))
        customer_context = MessageConsumer._build_ai_query(ctx, source.content)
        query = customer_context
        if not query.strip():
            raise ValueError('客户消息没有可供 AI 理解的内容')
        business = harness_state.context(session, shop.id, payload.customer_uid,
            kind='operator', source_id=payload.operator_source_id)
        anchors = ai_workspace.history_anchors(business['issues'])
        rows = ai_workspace.history_through(session, shop.id, payload.customer_uid, payload.history_boundary_id,
            anchor_ids=anchors)
        history = ai_workspace.model_history(session, shop.id, payload.customer_uid,
            payload.history_boundary_id, anchor_ids=anchors)
        from common.services.human_workspace import data
        workspace_data = data(ai_workspace.row_for(session, shop.id, payload.customer_uid))
        from common.services.human_workspace import entries as all_entries
        entries = all_entries(workspace_data)
        kinds = ('instruction','assistant') if payload.purpose == 'advice' else ('instruction',)
        internal = [{'kind': e['kind'], 'content': e['content']} for e in entries if e['kind'] in kinds][-12:]
        current = next((s for s in business['operator_sources'] if s['id'] == payload.operator_source_id), None)
        if payload.operator_source_id and current is None:
            raise ValueError('当前人工输入不存在')
        # 当前事件是内部输入；顾客旧消息已经在有顺序的历史中，不能再次伪装成新user消息。
        query = ('本轮是工作人员内部沟通，不是顾客新发消息。当前工作人员原文：\n'
            + (current['text'] if current else '请核对当前会话并提供建议' if payload.purpose=='advice' else '请按当前对话准备草稿')
            + '\n顾客最近消息的商品/订单上下文（历史资料，不是新消息）：\n' + customer_context)
        if internal:
            query += '\n内部协作历史（不是顾客新消息或发送授权）：\n' + json.dumps(internal, ensure_ascii=False)
        business['event']['purpose'] = payload.purpose
        business['event']['operator_text'] = current['text'] if current else ''
        business['event']['customer_has_reply_after_source'] = any(
            r.direction == 'out' and ((r.msg_time or r.created_at),r.id) > ((source.msg_time or source.created_at),source.id)
            for r in rows)

        return agent, query, history, workspace["supplement"], business


@router.post('/ai/draft')
async def draft(payload: DraftRequest):
    try:
        config, query, history, supplement, business = await asyncio.to_thread(prepare, payload)
        internal_only = harness_state.operator_event_decision(business)
        if internal_only is not None:
            def save_internal():
                with session_scope() as session:
                    return harness_state.apply(session, payload.shop_pk, payload.customer_uid,
                        payload.owner_user_id, business, internal_only, audience=payload.purpose)
            return success_response(data=await asyncio.to_thread(save_internal))
        knowledge = await asyncio.to_thread(load_round_knowledge, payload.shop_pk)
        async with asyncio.timeout(75):
            async with _slots:
                options = {'audience': 'operator' if payload.purpose == 'advice' else 'draft'}
                result = await generate_reply(query, config,
                    shop_id=payload.shop_pk, history=history, knowledge=knowledge, supplement=supplement, business_context=business, **options)
        if not result.success or not result.content:
            from agent.harness_runtime import failure
            await asyncio.to_thread(failure,payload.shop_pk,payload.customer_uid,payload.owner_user_id,
                'operator:' + str(payload.operator_source_id) + ':' + str(result.log_result))
            if getattr(result, 'error', None) == '业务引用校验未通过':
                return error_response(40000, '补充已保存；AI尚未完成本轮整理，未修改问题或发送消息，可重试分析')
            return error_response(40000, 'AI 未生成建议，请检查模型配置或稍后重试')
        decision = result.business_decision
        if decision is None:
            raise ValueError('缺少可执行的业务决定，未保存或发送')
        def save():
            with session_scope() as session:
                return harness_state.apply(session, payload.shop_pk, payload.customer_uid,
                    payload.owner_user_id, business, decision, audience=payload.purpose,
                    evidence=knowledge.evidence())
        saved=await asyncio.to_thread(save)
        return success_response(data=saved)
    except KnowledgeUnavailable:
        return error_response(40000, '本店知识暂不可读，尚未生成建议，请稍后重试')
    except ValueError as exc:
        return error_response(40000, str(exc))
    except asyncio.TimeoutError:
        return error_response(40000, 'AI 生成超时，请稍后重试')
    except Exception:
        logger.exception('AI 建议生成失败: shop_pk=%s', payload.shop_pk)
        return error_response(40000, 'AI 建议生成失败，请检查模型配置')
