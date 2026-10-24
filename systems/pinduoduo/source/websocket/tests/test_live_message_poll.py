"""补拉位置、失败游标和重启恢复；持久唯一性由共用SQL测试另行覆盖。"""
import asyncio
import pytest
from common.services import message_delivery as ledger
from channel_pdd import live_message_poll as live
from channel_pdd.pdd_message import Context, ContextType
from engine.message_consumer import MessageConsumer, ProcessOutcome

pytestmark=pytest.mark.usefixtures('delivery_db')


def test_duplicate_ws_and_poll_after_rebuild_only_consume_once(monkeypatch):
    calls=[]
    def build():
        consumer=MessageConsumer('s',2,2)
        async def process(_):calls.append(1);return ProcessOutcome(handled=True,log_result='no_match')
        monkeypatch.setattr(consumer,'_process',process)
        return consumer
    ctx=Context(type=ContextType.TEXT,content='fixture',kwargs={'from_user':'user','from_uid':'c','msg_id':'m'})
    asyncio.run(build().consume_raw(ctx));asyncio.run(build().consume_raw(ctx));assert calls==[1]
