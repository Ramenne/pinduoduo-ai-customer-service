import asyncio
from types import SimpleNamespace
from routes import ai_assist
from agent.agent_config import AgentConfig
from channel_pdd.pdd_message import Context, ContextType
from engine.message_consumer import MessageConsumer, ShopRuntime
from engine.reply_engine import ShopConfig, ReplyRules


def test_draft_returns_suggestion_without_sending(monkeypatch, delivery_db):
    calls=[]
    from common.models.log_models import ChatMessage
    from common.services import conversation_control
    with delivery_db.begin() as session:
        session.add(ChatMessage(id=7,shop_pk=1,customer_uid='c',direction='in',content='客户问题'))
        conversation_control.change(session,1,'c','human')
    from common.services import harness_state
    with delivery_db.begin() as session:
        business=harness_state.context(session,1,'c',kind='operator')
    monkeypatch.setattr(ai_assist,'prepare',lambda _: (AgentConfig(ai_enabled=True,api_key='fixture'), '客户问题', [], '人工依据',business))
    async def generate(*args,**kwargs):
        calls.append(kwargs);return SimpleNamespace(success=True,content='structured',business_decision={'version':'oin-business-1','message':'内部说明','customer_reply':'建议内容','issues':[{'id':'','topic':'问题','object_ref':'','action':'reply','question':'','reason':'已知事实','request_id':''}],'facts':[]})
    monkeypatch.setattr(ai_assist,'generate_reply',generate)
    payload=ai_assist.DraftRequest(shop_pk=1,owner_user_id=9,customer_uid='c',source_message_id=7,control_revision=1,history_boundary_id=7)
    result=asyncio.run(ai_assist.draft(payload))
    assert result.success and result.data['content']=='建议内容' and result.data['source_message_id']==7
    assert calls[0]['shop_id']==1 and calls[0]['supplement']=='人工依据'
    assert calls[0]['knowledge'].shop_pk==1
    assert '内部说明' not in result.data['content']


def test_invalid_source_never_calls_model(monkeypatch):
    monkeypatch.setattr(ai_assist,'prepare',lambda _: (_ for _ in ()).throw(ValueError('客户消息不存在或不属于当前会话')))
    async def generate(*args,**kwargs):raise AssertionError('must not call model')
    monkeypatch.setattr(ai_assist,'generate_reply',generate)
    payload=ai_assist.DraftRequest(shop_pk=1,owner_user_id=9,customer_uid='c',source_message_id=7,control_revision=1,history_boundary_id=7)
    assert not asyncio.run(ai_assist.draft(payload)).success


def test_pause_cancels_inflight_ai_send(monkeypatch, delivery_db):
    runtime=ShopRuntime(ShopConfig(shop_pk=2,ai_enabled=True),ReplyRules(),AgentConfig(ai_enabled=True,api_key='fixture'))
    consumer=MessageConsumer('s',2,2,runtime_loader=lambda _:runtime, conversation_control_reader=lambda *a: {'mode':'auto','revision':0})
    monkeypatch.setattr(consumer,'_forward_chat_event',lambda *a:None)
    monkeypatch.setattr(consumer,'_load_ai_history',lambda *a:[])
    async def generate(*args,**kwargs):
        runtime.shop_config.ai_enabled=False
        return SimpleNamespace(success=True,content='已生成',log_result='ai_reply')
    monkeypatch.setattr('engine.message_consumer.ai_reply_engine.generate_reply',generate)
    async def send(*args,**kwargs):raise AssertionError('paused AI must not send')
    monkeypatch.setattr(consumer,'_send_reply',send)
    result=asyncio.run(consumer._process(Context(type=ContextType.TEXT,content='你好',kwargs={'from_uid':'c'})))
    assert result.handled and not result.replied


def test_neutral_sticker_does_not_generate_or_send(monkeypatch, delivery_db):
    from agent.customer_voice import is_neutral_reaction
    runtime=ShopRuntime(ShopConfig(shop_pk=1,ai_enabled=True),ReplyRules(),AgentConfig(ai_enabled=True,api_key='fixture'))
    consumer=MessageConsumer('s',1,9,runtime_loader=lambda _:runtime,
        conversation_control_reader=lambda *a:{'mode':'auto','revision':0},log_writer=lambda *a:None)
    monkeypatch.setattr(consumer,'_forward_chat_event',lambda *a:None)
    async def forbidden(*args,**kwargs):raise AssertionError('standalone sticker must not generate or send')
    monkeypatch.setattr('engine.message_consumer.ai_reply_engine.generate_reply',forbidden)
    monkeypatch.setattr(consumer,'_send_reply',forbidden)
    result=asyncio.run(consumer.process(Context(type=ContextType.TEXT,content='[呆住]',kwargs={'from_uid':'c','msg_id':'neutral-reaction'})))
    assert result.log_result=='reaction_no_reply' and not result.replied
    assert not is_neutral_reaction('怎么还没发货？[呆住]')
    assert not is_neutral_reaction('[生气]')


def test_internal_ok_is_acknowledged_without_model_or_new_customer_draft(monkeypatch,delivery_db):
    from common.services import harness_state,conversation_control,ai_workspace
    from common.models.log_models import ChatMessage
    with delivery_db.begin() as s:
        s.add(ChatMessage(id=7,shop_pk=1,customer_uid='c',direction='in',content='你好'))
        conversation_control.change(s,1,'c','human')
        ai_workspace.save_instruction(s,1,'c','ok',1,9)
        business=harness_state.context(s,1,'c',kind='operator')
        business['event'].update(source_id=business['operator_sources'][-1]['id'],purpose='advice')
    monkeypatch.setattr(ai_assist,'prepare',lambda _: (AgentConfig(ai_enabled=True,api_key='fixture'),'内部ok',[],'',business))
    async def forbidden(*a,**k):raise AssertionError('ack must not regenerate old greeting')
    monkeypatch.setattr(ai_assist,'generate_reply',forbidden)
    payload=ai_assist.DraftRequest(shop_pk=1,owner_user_id=9,customer_uid='c',source_message_id=7,
        control_revision=2,history_boundary_id=7,purpose='advice')
    result=asyncio.run(ai_assist.draft(payload))
    assert result.success
    with delivery_db.begin() as s:
        saved=ai_workspace.read(s,1,'c');assert saved['draft'] is None
        assert '不会作为顾客的新消息' in saved['entries'][-1]['content']


def test_prepare_treats_operator_feedback_as_current_event_and_keeps_sent_reply(monkeypatch,delivery_db):
    from common.models.log_models import ChatMessage
    from common.models.config_models import LlmConfig
    from common.services import conversation_control,ai_workspace
    from datetime import datetime
    with delivery_db.begin() as s:
        s.add_all([ChatMessage(id=7,shop_pk=1,customer_uid='c',direction='in',msg_type='text',content='你好',msg_time=datetime(2026,10,22,19)),
            ChatMessage(id=8,shop_pk=1,customer_uid='c',direction='out',msg_type='text',content='您好，请问有什么需要帮助的',msg_time=datetime(2026,10,22,19,1)),
            LlmConfig(shop_pk=1,model_name='fixture',api_key_enc='fixture')])
        conversation_control.change(s,1,'c','human')
        ai_workspace.save_instruction(s,1,'c','你理解错了，这条昨天已经回过了',1,9)
        ident=ai_workspace.read(s,1,'c')['entries'][-1]['id']
    monkeypatch.setattr(ai_assist.AgentConfig,'from_llm_config',lambda _:AgentConfig(ai_enabled=True,api_key='fixture'))
    payload=ai_assist.DraftRequest(shop_pk=1,owner_user_id=9,customer_uid='c',source_message_id=7,
        control_revision=2,history_boundary_id=8,purpose='advice',operator_source_id=ident)
    _,query,history,_,ctx=ai_assist.prepare(payload)
    assert '当前工作人员原文：\n你理解错了' in query
    assert '不是顾客新发消息' in query
    assert [h['role'] for h in history]==['user','assistant']
    assert history[-1]['content']=='您好，请问有什么需要帮助的'
    assert ctx['event']['customer_has_reply_after_source'] is True
