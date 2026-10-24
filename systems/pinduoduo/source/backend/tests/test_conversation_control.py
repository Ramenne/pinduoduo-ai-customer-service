import pytest
"""原OIN单一接管权威的网页接线：持续冻结、代际隔离、失败关闭。"""
import asyncio
from types import SimpleNamespace
from sqlalchemy import select
from common.services import conversation_control as control
from common.models.conversation_control_models import ConversationControl
from app.services import chat_ai_service, chat_service, chat_send_client
from common.services.service_client import ServiceResponse
from .test_chat_workspace import env
from .test_chat_api import chat_env, _login_token, _auth_header
from engine.message_consumer import MessageConsumer, ShopRuntime
from engine.reply_engine import ShopConfig, ReplyRules
from agent.agent_config import AgentConfig
from channel_pdd.pdd_message import Context, ContextType


class Sender:
    def __init__(self, ok=True): self.calls=[]; self.ok=ok
    def send_text(self, uid, body):
        self.calls.append((uid, body)); return {'success':True} if self.ok else None


def consumer(pk, uid, *, ai=False, sender=None, keywords=None):
    runtime=ShopRuntime(ShopConfig(shop_pk=pk, ai_enabled=ai, default_reply_content='默认回复'),
                       ReplyRules(), AgentConfig(ai_enabled=ai, api_key='fixture'), keywords or [])
    logs=[]
    from common.db.session import session_scope
    from common.models.shop_models import Shop
    with session_scope() as s:shop_id=s.get(Shop,pk).shop_id
    c=MessageConsumer(shop_id,pk,uid,runtime_loader=lambda _:runtime, sender=sender or Sender(),
        log_writer=logs.append, chat_message_writer=lambda *a:None,chat_history_reader=lambda *a:[],
        control_event_sender=lambda *a:asyncio.sleep(0))
    c._forward_chat_event=lambda *a:None
    c._build_ai_query=lambda context,text:text
    return c, logs


def message(content='客户问题', uid='C1'):
    return Context(type=ContextType.TEXT, content=content, kwargs={'from_uid':uid,'msg_id':str(__import__('uuid').uuid4())})


def test_transfer_freezes_next_messages_and_new_consumer(db_session, env):
    data,user=env;pk=data['shop_a_pk'];sender=Sender()
    c,logs=consumer(pk,user.id,sender=sender,keywords=['人工'])
    c._transfer_service=SimpleNamespace(transfer_to_human=lambda *a,**k:SimpleNamespace(success=True))
    first=asyncio.run(c.process(message('我要人工')))
    second=asyncio.run(c.process(message('普通追问')))
    rebuilt,_=consumer(pk,user.id,sender=sender)
    third=asyncio.run(rebuilt.process(message('重新连接后的追问')))
    assert first.transferred and not second.replied and not third.replied and sender.calls==[]
    assert control.read_committed(pk,'C1')['mode']=='human'
    assert second.log_result=='human_owned'


def test_handoff_commit_failure_blocks_transfer_and_sending(db_session,env):
    data,user=env;c,_=consumer(data['shop_a_pk'],user.id,keywords=['人工'])
    def fail(*a):raise RuntimeError('storage unavailable')
    c._control_writer=fail
    c._transfer_service=SimpleNamespace(transfer_to_human=lambda *a,**k:1/0)
    result=asyncio.run(c.process(message('人工')))
    assert not result.replied and result.log_result=='control_unavailable'


def test_read_failure_never_assumes_auto(db_session,env):
    data,user=env;c,_=consumer(data['shop_a_pk'],user.id)
    c._control_reader=lambda *a:1/0
    result=asyncio.run(c.process(message()))
    assert not result.replied and result.log_result=='control_unavailable'


def test_takeover_then_resume_during_generation_fences_old_result(db_session,env,monkeypatch):
    data,user=env;pk=data['shop_a_pk'];sender=Sender();c,logs=consumer(pk,user.id,ai=True,sender=sender)
    async def generate(*a,**k):
        state=control.take_human(pk,'C1')
        from common.db.session import session_scope
        with session_scope() as s:control.change(s,pk,'C1','auto',expected_revision=state['revision'])
        return SimpleNamespace(success=True,content='过期生成结果',log_result='ai_reply')
    monkeypatch.setattr('engine.message_consumer.ai_reply_engine.generate_reply',generate)
    result=asyncio.run(c.process(message()))
    assert not result.replied and sender.calls==[] and control.read_committed(pk,'C1')['mode']=='auto'


def test_unconfirmed_send_is_not_ai_reply(db_session,env,monkeypatch):
    data,user=env;sender=Sender(ok=False);c,logs=consumer(data['shop_a_pk'],user.id,ai=True,sender=sender)
    async def generate(*a,**k):return SimpleNamespace(success=True,content='已生成但发送未确认',log_result='ai_reply')
    monkeypatch.setattr('engine.message_consumer.ai_reply_engine.generate_reply',generate)
    result=asyncio.run(c.process(message()))
    assert not result.replied and result.log_result=='auto_send_unconfirmed'
    assert logs[-1]['process_result']=='auto_send_unconfirmed'
    assert control.read_committed(data['shop_a_pk'],'C1')['mode']=='human'


def test_state_scopes_and_revision_conflict(db_session,env):
    data,user=env;pk=data['shop_a_pk']
    first=control.change(db_session,pk,'C1','human',expected_revision=0);db_session.commit()
    assert control.read_committed(pk,'C2')['mode']=='auto'
    assert control.read_committed(data['shop_b_pk'],'C1')['mode']=='auto'
    import pytest
    with pytest.raises(control.ControlConflict):control.change(db_session,pk,'C1','auto',expected_revision=0)
    assert control.read_committed(pk,'C1')['mode']=='human'
    control.change(db_session,pk,'C1','auto',expected_revision=first['revision']);db_session.commit()
    assert control.read_committed(pk,'C1')['revision']>first['revision']


def test_api_owner_permissions_and_cas(client,db_session,env,test_users):
    data,user=env;p=test_users['manager'];h=_auth_header(_login_token(client,p['username'],p['password']))
    path='/api/v1/chat/workspace/conversation-control'
    payload={'shop_pk':data['shop_a_pk'],'customer_uid':'C1'}
    state=client.get(path,params=payload,headers=h).json()
    assert state['success'] and state['data']['revision']==0
    result=client.post(path,json=payload|{'mode':'human','expected_revision':0},headers=h).json()
    assert result['success'] and result['data']['mode']=='human'
    assert not client.post(path,json=payload|{'mode':'auto','expected_revision':0},headers=h).json()['success']
    assert not client.get(path,params=payload|{'shop_pk':data['shop_b_pk']},headers=h).json()['success']
    assert not client.get(path,params=payload).json()['success']


def test_manual_send_failure_retains_committed_human_state(db_session,env,monkeypatch):
    data,user=env;pk=data['shop_a_pk']
    def fail(**kwargs):
        assert control.read_committed(pk,'C1')['mode']=='human'
        return SimpleNamespace(ok=False,message='发送未确认')
    monkeypatch.setattr(chat_send_client,'send_manual_message',fail)
    result=chat_service.send_message_by_uid(db_session,user,shop_pk=pk,customer_uid='C1',content='人工回复')
    assert not result.success and control.read_committed(pk,'C1')['mode']=='human'


def test_human_owned_conversation_can_generate_draft_without_auto_send(db_session,env,monkeypatch):
    data,user=env;pk=data['shop_a_pk'];control.take_human(pk,'C1')
    monkeypatch.setattr(chat_ai_service.service_client,'post_json',lambda *a,**k:
        ServiceResponse(ok=True,body={'success':True,'data':{'content':'供人工审核的建议','source_message_id':1}}))
    monkeypatch.setattr(chat_send_client,'send_manual_message',lambda **k:1/0)
    assert chat_ai_service.draft(db_session,user,pk,'C1').success
    assert control.read_committed(pk,'C1')['mode']=='human'


def test_model_failure_enters_visible_human_ownership(db_session,env,monkeypatch):
    data,user=env;pk=data['shop_a_pk'];c,logs=consumer(pk,user.id,ai=True)
    async def unavailable(*a,**k):
        return SimpleNamespace(success=False,content=None,log_result='ai_reply_failed')
    monkeypatch.setattr('engine.message_consumer.ai_reply_engine.generate_reply',unavailable)
    assert not asyncio.run(c.process(message())).replied
    state=control.read_committed(pk,'C1')
    assert state['mode']=='human' and 'AI' in state['reason']


@pytest.fixture(autouse=True)
def isolated_platform_baseline(monkeypatch):
    # 本组使用发送器桩，明确替换其平台读取端口；不冒称真实拼多多回读。
    monkeypatch.setattr('channel_pdd.reliable_delivery.read_baseline',lambda *a:{'ids':[],'at':0})
