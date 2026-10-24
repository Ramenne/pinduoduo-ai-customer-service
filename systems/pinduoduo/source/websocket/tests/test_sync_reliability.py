import asyncio
from sqlalchemy import select
from channel_pdd.api.get_product_list import GetProductList
from channel_pdd.core import read_budget
from channel_pdd.live_message_poll import LiveMessagePoll
from channel_pdd.pdd_message import Context, ContextType
from engine.message_consumer import MessageConsumer
from common.models.log_models import ChatMessage


def test_mobile_reply_is_persisted_once_without_model_or_send(delivery_db,monkeypatch):
    consumer = MessageConsumer('shop_1',1,9)
    monkeypatch.setattr(consumer,'_forward_chat_event',lambda *a,**k:None)
    context = Context(type=ContextType.TEXT,content='mobile reply',kwargs={
        'from_user':'mall_cs','to_uid':'c','msg_id':'mobile1','timestamp':1792661200})
    async def run():
        await consumer.persist_raw(context)
        await consumer.consume_raw(context)
        await consumer.consume_raw(context)
    asyncio.run(run())
    with delivery_db() as s:
        rows=list(s.scalars(select(ChatMessage)))
        assert len(rows)==1 and rows[0].direction=='out' and rows[0].content=='mobile reply'


def test_empty_complete_catalog_is_valid_but_partial_or_duplicate_is_failure(monkeypatch):
    fetcher=GetProductList()
    monkeypatch.setattr(fetcher,'fetch_page',lambda **kw:{'success':True,'products':[],'total':0})
    assert fetcher.fetch_all()['complete'] is True
    monkeypatch.setattr('channel_pdd.api.get_product_list.time.sleep',lambda _:None)
    def page(**kw):
        if kw['page']==1:return {'success':True,'products':[{'goods_id':'a'}],'total':2}
        return {'success':False}
    monkeypatch.setattr(fetcher,'fetch_page',page)
    assert fetcher.fetch_all()['success'] is False
    monkeypatch.setattr(fetcher,'fetch_page',lambda **kw:{'success':True,'products':[{'goods_id':'a'}],'total':2})
    assert fetcher.fetch_all()['success'] is False


def test_malformed_catalog_is_not_empty_snapshot(monkeypatch):
    fetcher=GetProductList()
    monkeypatch.setattr(fetcher,'post',lambda *a,**kw:{'success':True,'result':{}})
    assert fetcher.fetch_page()['success'] is False


def test_shared_read_budget_cools_down_without_replaying_or_cross_shop_block(monkeypatch):
    monkeypatch.setattr(read_budget,'_shops',{})
    now=[100.0];calls=[]
    monkeypatch.setattr(read_budget.time,'monotonic',lambda:now[0])
    monkeypatch.setattr(read_budget.time,'sleep',lambda delay:now.__setitem__(0,now[0]+delay))
    def rejected():calls.append(1);return {'success':False},429
    read_budget.execute(9,'s',rejected)
    assert read_budget.execute(9,'s',rejected)['success'] is False
    assert calls==[1]
    assert read_budget.execute(9,'other',lambda:({'success':True},200))['success'] is True
    now[0]+=301
    read_budget.execute(9,'s',lambda:({'success':True},200))
    before=now[0]
    read_budget.execute(9,'s',lambda:({'success':True},200))
    assert now[0]-before==2
