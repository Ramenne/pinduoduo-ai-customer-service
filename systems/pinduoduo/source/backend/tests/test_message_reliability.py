"""真实SQL事务验证重启、崩溃和核对边界；平台效果使用适配器注入。"""
import asyncio
import time
import pytest
from sqlalchemy import update
from common.db.session import session_scope
from common.models.message_delivery_models import MessageInbox, MessageDelivery
from common.services import message_delivery as ledger, conversation_control as control
from channel_pdd.reliable_delivery import dispatch
from channel_pdd.pdd_message import Context, ContextType
from engine.message_consumer import MessageConsumer, ShopRuntime
from engine.reply_engine import ShopConfig, ReplyRules
from .test_chat_workspace import env
from .test_chat_api import chat_env


def payload(mid='m1'):
    return {'type':'text','content':'客户问题','kwargs':{'msg_id':mid,'from_uid':'C1','from_user':'user'}}


def test_database_dedupe_rebuild_and_scopes(db_session):
    first=ledger.receive(1,'C1','m1',payload())
    assert ledger.receive(1,'C1','m1',payload())==first
    assert ledger.receive(2,'C1','m1',payload())!=first
    assert ledger.receive(1,'C2','m1',payload())!=first
    assert ledger.receive(1,'C1','M1',payload())!=first
    handle=ledger.claim(first);assert ledger.claim(first) is None
    ledger.finish(handle);assert ledger.claim(first,now=time.time()+10000) is None


def test_lease_reclaim_fences_old_sender_and_old_finish(db_session):
    ident=ledger.receive(1,'C1','m1',payload());old=ledger.claim(ident)
    with session_scope() as s:s.execute(update(MessageInbox).where(MessageInbox.id==ident).values(due_at=0))
    current=ledger.claim(ident)
    with pytest.raises(ValueError):ledger.reserve(1,'C1','auto:1:reply','text','body',handle=old)
    ledger.finish(old)
    with session_scope() as s:assert s.get(MessageInbox,ident).token==current[1]


def test_same_conversation_processing_order(db_session):
    a=ledger.receive(1,'C1','m1',payload());b=ledger.receive(1,'C1','m2',payload('m2'))
    assert ledger.claim(b) is None
    handle=ledger.claim(a);ledger.finish(handle);assert ledger.claim(b)


def test_crash_after_dispatch_not_replayed(db_session):
    ident=ledger.receive(1,'C1','m1',payload());handle=ledger.claim(ident)
    row,fresh=ledger.reserve(1,'C1','auto:1:reply','text','body',handle=handle);assert fresh
    with session_scope() as s:s.execute(update(MessageInbox).where(MessageInbox.id==ident).values(due_at=0))
    assert ledger.claim(ident) is None
    with session_scope() as s:assert s.get(MessageInbox,ident).state=='review'
    again,fresh=ledger.reserve(1,'C1','auto:1:reply','text','body');assert not fresh and again['state']=='dispatching'
    with pytest.raises(ValueError):ledger.reserve(1,'C1','new-input','text','other',revision=0)


def test_uncertain_does_not_retry_and_blocks_resume(db_session):
    calls=[]
    def send():calls.append(1);return None
    one=dispatch(1,'C1','manual:unique-key','text','body',send)
    two=dispatch(1,'C1','manual:unique-key','text','body',send)
    assert len(calls)==1 and one['state']==two['state']=='uncertain'
    assert control.read_committed(1,'C1')['mode']=='human'
    with pytest.raises(control.ControlConflict):control.change(db_session,1,'C1','auto')
    with pytest.raises(ValueError):dispatch(1,'C1','manual:unique-key','text','changed',send)


def test_strong_receipt_only_and_late_failure_cannot_regress(db_session):
    row=dispatch(1,'C1','manual:key','text','body',lambda:{'success':True,'result':{'msg_id':'p1'}})
    def msg(mid,uid='C1',role='mall_cs'):return {'msg_id':mid,'content':'body','from':{'role':role},'to':{'uid':uid}}
    ledger.check_result(row['id'],[msg('other'),msg('p1','C2'),msg('p1',role='user')])
    with session_scope() as s:assert s.get(MessageDelivery,row['id']).state=='accepted'
    ledger.check_result(row['id'],[msg('p1')]);assert ledger.settle(row['id'],None)['state']=='confirmed'


def test_unmatched_becomes_review_then_audited_resolution(db_session):
    row=dispatch(1,'C1','manual:key','text','body',lambda:None)
    for _ in range(5):ledger.check_result(row['id'],[],readable=False)
    with session_scope() as s:
        assert s.get(MessageDelivery,row['id']).state=='review'
        ledger.resolve(s,1,'C1',row['id'],'not_sent',actor_id=7,note='在平台人工核实确未发出')
    with session_scope() as s:assert s.get(MessageDelivery,row['id']).resolved_by==7
    assert control.read_committed(1,'C1')['mode']=='human'
    with session_scope() as s:control.change(s,1,'C1','auto')


def test_request_echo_confirms_but_does_not_release_human(db_session):
    row=dispatch(1,'C1','manual:key','text','body',lambda:None)
    ledger.check_result(row['id'],[{'msg_id':'p1','request_id':int(row['request_id']),
        'from':{'role':'mall_cs'},'to':{'uid':'C1'}}])
    with session_scope() as s:assert s.get(MessageDelivery,row['id']).state=='confirmed'
    assert control.read_committed(1,'C1')['mode']=='human'


def test_poll_position_survives_restart_and_never_regresses(db_session):
    assert ledger.poll_position(1,1000)==1000
    ledger.advance_poll(1,1100);ledger.advance_poll(1,1050);assert ledger.poll_position(1,2000)==1100


def test_durable_consumer_ws_poll_restart_single_send(db_session,env):
    data,user=env;pk=data['shop_a_pk'];calls=[]
    class Sender:
        def send_text(self,uid,body):calls.append(1);return {'success':True,'result':{'msg_id':'out1'}}
    def build():
        from common.models.shop_models import Shop
        c=MessageConsumer(db_session.get(Shop,pk).shop_id,pk,user.id,sender=Sender(),
            runtime_loader=lambda _:ShopRuntime(ShopConfig(shop_pk=pk,default_reply_content='您好'),ReplyRules()),
            chat_message_writer=lambda *a:None,log_writer=lambda *a:None)
        c._forward_chat_event=lambda *a:None
        return c
    ctx=Context(type=ContextType.TEXT,content='客户问题',kwargs={'from_uid':'C1','from_user':'user','msg_id':'stable-id'})
    async def run():
        first=await build().consume_raw(ctx);second=await build().consume_raw(ctx)
        assert first.replied and not second.replied
    asyncio.run(run());assert len(calls)==1


def test_owner_changed_before_dispatch_blocks_effect(db_session,env):
    data,user=env;calls=[]
    with pytest.raises(ValueError):dispatch(data['shop_a_pk'],'C1','manual:key','text','body',lambda:calls.append(1),owner_id=user.id+100)
    assert calls==[]


def test_recovery_api_scope_and_resolution(client,db_session,env,test_users):
    from .test_chat_api import _login_token,_auth_header
    data,user=env;pk=data['shop_a_pk'];row=dispatch(pk,'C1','manual:key','text','body',lambda:None)
    headers=_auth_header(_login_token(client,test_users['manager']['username'],test_users['manager']['password']))
    path='/api/v1/chat/workspace/deliveries'
    response=client.get(path,params={'shop_pk':pk,'customer_uid':'C1'},headers=headers).json()
    assert response['success'] and response['data'][0]['id']==row['id']
    assert not client.get(path,params={'shop_pk':data['shop_b_pk'],'customer_uid':'C1'},headers=headers).json()['success']
    result=client.post(path+'/resolve',json={'shop_pk':pk,'customer_uid':'C1','delivery_id':row['id'],
        'outcome':'confirmed','note':'平台记录已看到此消息'},headers=headers).json()
    assert result['success'] and result['data']['state']=='confirmed'


def test_lost_http_response_not_replayed_and_history_echo_reconciles(db_session,monkeypatch):
    import json,threading
    from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
    from channel_pdd.api import send_message as api
    from channel_pdd import reliable_delivery
    captured=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            captured.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            self.close_connection=True  # 已接收操作，但在回执送出前断线。
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    monkeypatch.setattr(api,'SEND_MESSAGE_URL',f'http://127.0.0.1:{server.server_port}/synthetic-send')
    sender=api.SendMessage()
    try:
        one=dispatch(1,'C1','manual:lost-response','text','synthetic',lambda:sender.send_text('C1','synthetic'))
        two=dispatch(1,'C1','manual:lost-response','text','synthetic',lambda:sender.send_text('C1','synthetic'))
        assert one['state']==two['state']=='uncertain' and len(captured)==1
        request_id=captured[0]['data']['request_id']
        assert isinstance(request_id,int) and str(request_id)==one['request_id']
        with session_scope() as s:s.execute(update(MessageDelivery).where(MessageDelivery.id==one['id']).values(due_at=0))
        monkeypatch.setattr('channel_pdd.api.get_chat_history.GetChatHistory.__init__',lambda *a,**k:None)
        monkeypatch.setattr('channel_pdd.api.get_chat_history.GetChatHistory.fetch_page',lambda *a,**k:{'success':True,
            'messages':[{'msg_id':'platform-id','request_id':request_id,'from':{'role':'mall_cs'},'to':{'uid':'C1'}}]})
        reliable_delivery.reconcile(1,'synthetic',1)
        with session_scope() as s:assert s.get(MessageDelivery,one['id']).state=='confirmed'
        assert len(captured)==1 and control.read_committed(1,'C1')['mode']=='human'
    finally:
        server.shutdown();server.server_close();thread.join(2)


def test_inbound_projection_retry_does_not_duplicate_unread(db_session,env):
    from common.models.log_models import ChatMessage,Conversation
    from common.models.shop_models import Shop
    from sqlalchemy import select
    data,user=env;pk=data['shop_a_pk'];count=[0]
    before=db_session.scalar(select(Conversation).where(Conversation.shop_pk==pk,Conversation.customer_uid=='C1')).unread_count
    def runtime(_):
        count[0]+=1
        if count[0]==1:raise RuntimeError('temporary config failure')
        return ShopRuntime(ShopConfig(shop_pk=pk),ReplyRules())
    c=MessageConsumer(db_session.get(Shop,pk).shop_id,pk,user.id,runtime_loader=runtime,log_writer=lambda *a:None)
    c._forward_chat_event=lambda *a:None
    ctx=Context(type=ContextType.TEXT,content='fixture',kwargs={'from_uid':'C1','from_user':'user','msg_id':'retry-projection'})
    asyncio.run(c.consume_raw(ctx))
    with session_scope() as s:s.execute(update(MessageInbox).where(MessageInbox.message_id=='retry-projection').values(due_at=0))
    asyncio.run(c.consume_raw(ctx))
    with session_scope() as s:
        assert len(list(s.scalars(select(ChatMessage).where(ChatMessage.msg_id=='retry-projection'))))==1
        conv=s.scalar(select(Conversation).where(Conversation.shop_pk==pk,Conversation.customer_uid=='C1'))
        assert conv.unread_count==before+1


def test_legacy_history_without_processing_proof_is_not_replayed(db_session):
    from common.models.log_models import ChatMessage
    db_session.add(ChatMessage(shop_pk=1,customer_uid='C1',msg_id='legacy-id',direction='in',content='old'))
    db_session.commit()
    ident=ledger.receive(1,'C1','legacy-id',payload())
    assert ledger.claim(ident) is None
    with session_scope() as s:assert s.get(MessageInbox,ident).state=='review'
    assert control.read_committed(1,'C1')['mode']=='human'


@pytest.mark.parametrize('missing_signature',[False,True])
def test_card_uncertainty_never_falls_back_but_preflight_missing_signature_can(db_session,env,missing_signature):
    from common.models.shop_models import Shop
    from channel_pdd.core.anti_content import AntiContentMissingError
    from engine.reply_engine import ReplyDecision,ACTION_GOODS_SPECIFIC
    data,user=env;pk=data['shop_a_pk'];calls=[]
    class Sender:
        def ensure_anti_content(self):
            if missing_signature:raise AntiContentMissingError()
        def send_mall_goods_card(self,*a):calls.append('card');return None
        def send_text(self,*a):calls.append('text');return {'success':True}
    c=MessageConsumer(db_session.get(Shop,pk).shop_id,pk,user.id,sender=Sender(),
        log_writer=lambda *a:None,chat_message_writer=lambda *a:None,control_event_sender=lambda *a:asyncio.sleep(0))
    async def process(ctx):return await c._handle_goods_card(ctx,'C1',ReplyDecision(ACTION_GOODS_SPECIFIC,log_result='auto_reply'),0)
    c._process=process
    ctx=Context(type=ContextType.GOODS_INQUIRY,content={'goods_id':'g1'},goods_context={'goods_id':'g1','goods_name':'fixture'},
        kwargs={'from_uid':'C1','from_user':'user','msg_id':'card-boundary'})
    result=asyncio.run(c.consume_raw(ctx))
    assert calls==(['text'] if missing_signature else ['card'])
    assert result.replied==missing_signature


@pytest.fixture(autouse=True)
def isolated_platform_baseline(monkeypatch):
    # 本组使用发送器桩，明确替换其平台读取端口；不冒称真实拼多多回读。
    monkeypatch.setattr('channel_pdd.reliable_delivery.read_baseline',lambda *a:{'ids':[],'at':0})


def test_oin_single_verdict_fails_closed_and_acceptance_is_not_delivery():
    from common.services.send_verdict import from_state,SendVerdict
    assert from_state('accepted')==SendVerdict.IN_FLIGHT
    assert from_state('confirmed')==SendVerdict.DELIVERED
    assert from_state('new-unclassified-state')==SendVerdict.MAY_HAVE_LEFT
    assert from_state('review')==SendVerdict.MAY_HAVE_LEFT


def test_oin_baseline_readback_excludes_old_and_requires_unique_candidate(db_session):
    baseline={'ids':['old'],'at':1000}
    row,_=ledger.reserve(1,'C1','manual:baseline','text','same',baseline=baseline)
    ledger.settle(row['id'],{'success':True})
    def msg(mid,ts=1000):return {'msg_id':mid,'ts':ts,'type':0,'content':'same','from':{'role':'mall_cs'},'to':{'uid':'C1'}}
    ledger.check_result(row['id'],[msg('old'),msg('old-other',999)])
    with session_scope() as s:assert s.get(MessageDelivery,row['id']).state=='accepted'
    ledger.check_result(row['id'],[msg('new-a'),msg('new-b')])
    with session_scope() as s:assert s.get(MessageDelivery,row['id']).state=='review'
    row,_=ledger.reserve(1,'C1','manual:baseline-unique','text','same',baseline=baseline)
    ledger.settle(row['id'],{'success':True})
    ledger.check_result(row['id'],[msg('old'),msg('unique-new')])
    with session_scope() as s:
        result=s.get(MessageDelivery,row['id']);assert result.state=='confirmed' and result.platform_id=='unique-new'


def test_database_clock_authority_ignores_worker_clock_skew(db_session,monkeypatch):
    from types import SimpleNamespace
    ident=ledger.receive(1,'C1','clock-id',payload())
    monkeypatch.setattr(ledger,'time',SimpleNamespace(time=lambda:10**12,sleep=lambda _:None))
    handle=ledger.claim(ident)
    with session_scope() as s:assert s.get(MessageInbox,ident).due_at<10**10
    row,fresh=ledger.reserve(1,'C1','auto:clock','text','body',handle=handle,revision=0)
    assert fresh and row['state']=='dispatching'


def test_pre_send_lease_failure_is_recoverable_not_completed(db_session,env):
    from common.models.shop_models import Shop
    from sqlalchemy import select
    data,user=env;pk=data['shop_a_pk'];calls=[]
    def runner(*args,**kwargs):
        raise ledger.LeaseLost('lease changed before any POST')
    class Sender:
        def send_text(self,*args):calls.append(1)
    c=MessageConsumer(db_session.get(Shop,pk).shop_id,pk,user.id,sender=Sender(),delivery_runner=runner,
        runtime_loader=lambda _:ShopRuntime(ShopConfig(shop_pk=pk,default_reply_content='您好'),ReplyRules()),
        chat_message_writer=lambda *a:None,log_writer=lambda *a:None)
    c._forward_chat_event=lambda *a:None
    ctx=Context(type=ContextType.TEXT,content='fixture',kwargs={'from_uid':'C1','from_user':'user','msg_id':'pre-send-lease-failure'})
    result=asyncio.run(c.consume_raw(ctx))
    assert result.log_result=='processing_error' and not result.replied and calls==[]
    with session_scope() as s:
        row=s.scalar(select(MessageInbox).where(MessageInbox.shop_pk==pk,MessageInbox.message_id=='pre-send-lease-failure'))
        assert row.state=='pending' and row.result=='processing_error'


def test_legacy_failed_input_flag_preserves_existing_effect(db_session):
    from common.db.upgrade_recovery_clocks import flag_failed_inputs
    first=ledger.receive(1,'C1','missing-effect',payload('missing-effect'))
    ledger.finish(ledger.claim(first),'auto_send_unconfirmed')
    second=ledger.receive(1,'C2','existing-effect',payload('existing-effect'))
    handle=ledger.claim(second)
    ledger.reserve(1,'C2','effect-once','text','body',handle=handle)
    ledger.finish(handle,'auto_send_unconfirmed')
    assert flag_failed_inputs(db_session)==[first]
    db_session.commit()
    with session_scope() as s:
        assert s.get(MessageInbox,first).state=='review'
        assert s.get(MessageInbox,second).state=='done'
        assert flag_failed_inputs(s)==[]


def test_new_customer_message_blocks_pinned_ai_send_before_effect(db_session,env):
    from common.services import ai_workspace
    from common.models.log_models import ChatMessage
    from sqlalchemy import select,func
    data,user=env;pk=data['shop_a_pk']
    boundary=ai_workspace.latest_message(db_session,pk,'C1')
    db_session.add(ChatMessage(shop_pk=pk,customer_uid='C1',direction='in',msg_type='text',content='新的问题'))
    db_session.commit()
    with pytest.raises(ValueError,match='对话已变化'):
        ledger.reserve(pk,'C1','pinned-ai-command','text','旧草稿',revision=0,history_boundary_id=boundary)
    assert db_session.scalar(select(func.count()).select_from(MessageDelivery))==0


def test_pending_receipt_waits_without_completing_new_customer_input(db_session,env):
    from common.models.shop_models import Shop
    data,user=env;pk=data['shop_a_pk']
    first,_=ledger.reserve(pk,'C1','manual:pending-proof','text','earlier')
    c=MessageConsumer(db_session.get(Shop,pk).shop_id,pk,user.id,
        runtime_loader=lambda _:ShopRuntime(ShopConfig(shop_pk=pk,default_reply_content='reply'),ReplyRules()),
        chat_message_writer=lambda *a:None,log_writer=lambda *a:None)
    c._forward_chat_event=lambda *a:None
    ctx=Context(type=ContextType.TEXT,content='new',kwargs={'from_uid':'C1','msg_id':'while-proof-pending'})
    result=asyncio.run(c.consume_raw(ctx));assert result.log_result=='send_pending'
    with session_scope() as s:
        from sqlalchemy import select
        item=s.scalar(select(MessageInbox).where(MessageInbox.message_id=='while-proof-pending'))
        assert item.state=='pending' and item.attempts==0
    ledger.check_result(first['id'],[{'msg_id':'earlier-id','request_id':first['request_id'],'from':{'role':'mall_cs'},'to':{'uid':'C1'}}])


def test_precommit_read_failure_keeps_inbox_recoverable_without_send(db_session,env,monkeypatch):
    from channel_pdd.reliable_delivery import PreparationUnavailable
    from common.models.shop_models import Shop
    data,user=env;pk=data['shop_a_pk'];calls=[]
    class Sender:
        def send_text(self,*a):calls.append(1);return {'success':True}
    def unavailable(*a):raise PreparationUnavailable('synthetic read unavailable')
    monkeypatch.setattr('channel_pdd.reliable_delivery.read_baseline',unavailable)
    c=MessageConsumer(db_session.get(Shop,pk).shop_id,pk,user.id,sender=Sender(),
        runtime_loader=lambda _:ShopRuntime(ShopConfig(shop_pk=pk,default_reply_content='hello'),ReplyRules()),
        chat_message_writer=lambda *a:None,log_writer=lambda *a:None)
    c._forward_chat_event=lambda *a:None
    ctx=Context(type=ContextType.TEXT,content='new',kwargs={'from_uid':'C1','msg_id':'precommit-read'})
    asyncio.run(c.consume_raw(ctx))
    with session_scope() as s:
        from sqlalchemy import select
        item=s.scalar(select(MessageInbox).where(MessageInbox.message_id=='precommit-read'))
        assert item.state=='pending' and not list(s.scalars(select(MessageDelivery)))
        item.due_at=0
    monkeypatch.setattr('channel_pdd.reliable_delivery.read_baseline',lambda *a:{'ids':[],'at':0})
    assert asyncio.run(c.consume_raw(ctx)).replied and calls==[1]


def test_expired_executor_cannot_take_human_or_finish(db_session):
    ident=ledger.receive(1,'C1','expired-owner',payload());handle=ledger.claim(ident)
    with session_scope() as s:s.execute(update(MessageInbox).where(MessageInbox.id==ident).values(due_at=0))
    token=ledger.current_inbox.set(handle)
    try:
        with pytest.raises(ValueError):control.take_human(1,'C1','stale executor',0)
        ledger.finish(handle)
    finally:ledger.current_inbox.reset(token)
    with session_scope() as s:assert s.get(MessageInbox,ident).state=='processing'
    assert control.read_committed(1,'C1')['mode']=='auto'


def test_manual_preparation_is_fenced_if_user_resumed_auto(db_session):
    with session_scope() as s:state=control.change(s,1,'C1','human')
    with session_scope() as s:control.change(s,1,'C1','auto',expected_revision=state['revision'])
    calls=[]
    with pytest.raises(ValueError):dispatch(1,'C1','manual:stale-human-intent','text','body',lambda:calls.append(1),
        revision=state['revision'],expected_mode='human')
    assert calls==[]
