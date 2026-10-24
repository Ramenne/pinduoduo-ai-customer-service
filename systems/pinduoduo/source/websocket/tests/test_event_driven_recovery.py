import asyncio
import pytest
from sqlalchemy import select,update
from common.models.message_delivery_models import MessageDelivery
from common.models.log_models import ChatMessage
from common.services import message_delivery as ledger
from channel_pdd import reliable_delivery as delivery
from channel_pdd import live_message_poll as live
from channel_pdd.live_message_poll import LiveMessagePoll
from common.utils.time_utils import now_beijing_naive

pytestmark=pytest.mark.usefixtures('delivery_db')


def test_idle_recovery_does_not_query_platform(monkeypatch):
    async def enqueue(raw):pass
    poll=LiveMessagePoll('shop_1',1,9,enqueue)
    monkeypatch.setattr(live,'authorized',lambda *a:True)
    monkeypatch.setattr(poll.gap,'fetch_list',lambda *a:(_ for _ in ()).throw(AssertionError('idle platform query')))
    asyncio.run(poll.check(pull=False));asyncio.run(poll.check(pull=False))


def test_only_connection_event_requests_one_pull_and_reconnect_bursts_merge(monkeypatch):
    now=[1000]
    monkeypatch.setattr(live.time,'monotonic',lambda:now[0])
    poll=LiveMessagePoll('s',1,9,None)
    assert poll.take_pull() is False
    poll.connected();assert poll.take_pull() is True
    assert poll.take_pull() is False
    now[0]+=5;poll.connected();poll.connected();assert poll.take_pull() is False
    now[0]+=56;assert poll.take_pull() is True
    now[0]+=3600;assert poll.take_pull() is False


def test_receipt_push_confirms_exact_request_only(delivery_db):
    row,_=ledger.reserve(1,'c','push:receipt','text','same')
    assert delivery.observe_push(1,{'message':{'msg_id':'other','from':{'role':'mall_cs'},'to':{'uid':'c'},'content':'same'}}) is False
    wrong={'message':{'msg_id':'m','request_id':row['request_id'],'from':{'role':'mall_cs'},'to':{'uid':'different'}}}
    assert delivery.observe_push(1,wrong) is False
    assert delivery.observe_push(2,{**wrong,'message':{**wrong['message'],'to':{'uid':'c'}}}) is False
    right={**wrong,'message':{**wrong['message'],'to':{'uid':'c'}}}
    assert delivery.observe_push(1,right) is True
    assert delivery.observe_push(1,right) is False
    with delivery_db() as s:assert s.get(MessageDelivery,row['id']).state=='confirmed'


def test_local_platform_id_confirmation_avoids_http(monkeypatch,delivery_db):
    row,_=ledger.reserve(1,'c','local:receipt','text','hello')
    ledger.settle(row['id'],{'success':True,'result':{'msg_id':'platform1'}})
    with delivery_db.begin() as s:
        s.add(ChatMessage(shop_pk=1,customer_uid='c',msg_id='platform1',direction='out',content='hello',msg_time=now_beijing_naive()))
        s.execute(update(MessageDelivery).where(MessageDelivery.id==row['id']).values(due_at=0))
    monkeypatch.setattr('channel_pdd.api.get_chat_history.GetChatHistory.__init__',lambda *a,**k:(_ for _ in ()).throw(AssertionError('unneeded HTTP')))
    delivery.reconcile(1,'shop_1',9)
    with delivery_db() as s:assert s.get(MessageDelivery,row['id']).state=='confirmed'


def test_uncertain_send_gets_only_one_bounded_history_query(monkeypatch,delivery_db):
    row,_=ledger.reserve(1,'c','uncertain:once','text','hello')
    ledger.settle(row['id'],None)
    calls=[]
    monkeypatch.setattr('channel_pdd.api.get_chat_history.GetChatHistory.__init__',lambda *a,**k:None)
    monkeypatch.setattr('channel_pdd.api.get_chat_history.GetChatHistory.fetch_page',lambda *a,**k:(calls.append(1) or {'success':True,'messages':[],'has_more':False}))
    for _ in range(3):
        with delivery_db.begin() as s:s.execute(update(MessageDelivery).where(MessageDelivery.id==row['id']).values(due_at=0))
        delivery.reconcile(1,'shop_1',9)
    assert calls==[1]
    with delivery_db() as s:assert s.get(MessageDelivery,row['id']).state=='uncertain'


def test_unconfirmed_receipt_deadline_requires_human_without_resend(delivery_db):
    from datetime import timedelta
    row,_=ledger.reserve(1,'c','deadline:receipt','text','hello')
    with delivery_db.begin() as s:
        s.execute(update(MessageDelivery).where(MessageDelivery.id==row['id']).values(created_at=now_beijing_naive()-timedelta(minutes=6)))
    assert ledger.receipt_deadline(row['id']) is True
    with delivery_db() as s:assert s.get(MessageDelivery,row['id']).state=='review'


def test_healthy_channel_does_not_start_periodic_browser_maintenance(monkeypatch):
    from channel_pdd.pdd_channel import PDDChannel
    from channel_pdd.session_health import SessionMaintenance
    async def wait():await asyncio.Event().wait()
    async def forbidden():raise AssertionError('periodic maintenance was started')
    channel=PDDChannel(shop_id='shop_1',user_id=9)
    channel.session_maintenance=SessionMaintenance('shop_1',9)
    monkeypatch.setattr(channel,'_connect_with_retry',wait)
    monkeypatch.setattr(channel.session_maintenance,'run',forbidden)
    async def run():
        await channel.start();await asyncio.sleep(0)
        assert channel._maintenance_task is None
        await channel.stop()
    asyncio.run(run())


def test_accepted_send_is_success_and_does_not_block_following_input(delivery_db):
    from datetime import timedelta
    first,_=ledger.reserve(1,'c','accepted:success','text','hello')
    ledger.settle(first['id'],{'success':True})
    assert ledger.waiting_for_receipt(1,'c') is False
    second,fresh=ledger.reserve(1,'c','accepted:next','text','next',revision=0)
    assert fresh and second['id']!=first['id']
    with delivery_db.begin() as s:
        s.execute(update(MessageDelivery).where(MessageDelivery.id==first['id']).values(created_at=now_beijing_naive()-timedelta(minutes=6)))
    assert ledger.receipt_deadline(first['id']) is False


def test_accepted_send_does_not_query_history_for_extra_receipt(monkeypatch,delivery_db):
    first,_=ledger.reserve(1,'c','accepted:no-http','text','hello')
    ledger.settle(first['id'],{'success':True})
    with delivery_db.begin() as s:s.execute(update(MessageDelivery).where(MessageDelivery.id==first['id']).values(due_at=0))
    monkeypatch.setattr('channel_pdd.api.get_chat_history.GetChatHistory.__init__',lambda *a,**k:(_ for _ in ()).throw(AssertionError('accepted send should not query')))
    delivery.reconcile(1,'shop_1',9)
    with delivery_db() as s:assert s.get(MessageDelivery,first['id']).state=='accepted'
