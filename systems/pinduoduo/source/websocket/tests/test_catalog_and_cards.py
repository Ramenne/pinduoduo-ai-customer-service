import asyncio
from types import SimpleNamespace
from agent import ai_reply_engine
from agent.agent_config import AgentConfig
from agent.llm_client import LLMResponse
from channel_pdd.pdd_message import ContextType,PDDChatMessage
from channel_pdd.api.get_chat_history import GetChatHistory
from engine.message_consumer import MessageConsumer


def test_extended_card_fields_recovered_without_guessing_type():
    raw={'type':31,'content':'{"goodsInfo":{"goodsId":"g1","goodsName":"蜜薯"}}','from':{'role':'user','uid':'c'},'msg_id':'m'}
    ctx=PDDChatMessage({'response':'push','message':raw}).to_context()
    assert ctx.type==ContextType.GOODS_INQUIRY
    assert ctx.goods_context=={'goods_id':'g1','goods_name':'蜜薯'}
    parsed=GetChatHistory._parse_message(raw)
    assert parsed['goods_context']['goods_id']=='g1' and '蜜薯' in parsed['content']
    for typ in (31,41,51):
        ctx=PDDChatMessage({'response':'push','message':{'type':typ,'content':None}}).to_context()
        assert ctx.type==ContextType.SYSTEM_STATUS and ctx.content=='[平台扩展消息]'


def test_catalog_is_always_in_ai_context(monkeypatch):
    seen=[];shops=[]
    monkeypatch.setattr(ai_reply_engine,'list_shop_products',lambda params:shops.append(params['shop_id']) or '本店目录共1件：云南蜜薯；库存未核实')
    class Client:
        tools=[]
        async def chat(self,messages,**kwargs):
            seen.extend(messages);return LLMResponse(content='您说的是蜜薯这款吗？库存需要确认。')
    result=asyncio.run(ai_reply_engine.generate_reply('你家不就一款？',AgentConfig(ai_enabled=True,api_key='fixture'),shop_id=2,client=Client()))
    assert result.success and shops==[2]
    assert '云南蜜薯' in seen[0]['content'] and '库存未核实' in seen[0]['content']


def test_parser_error_placeholders_not_sent_to_ai_history():
    consumer=MessageConsumer('s',2,2,chat_history_reader=lambda *args:[
        {'direction':'in','content':'不支持的消息类型: 41','msg_type':'system_status'},
        {'direction':'in','content':'平台通知','msg_type':'system_status'},
        {'direction':'in','content':'上一条问题','msg_type':'text'},
        {'direction':'in','content':'当前问题','msg_type':'text'}], product_context_reader=lambda *args:None)
    history=consumer._load_ai_history('c')
    assert history==[{'role':'user','content':'上一条问题'}]
