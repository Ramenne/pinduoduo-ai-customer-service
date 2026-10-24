"""上下文失败不回答、知识固定、人工补充/草稿持久与代际隔离。"""
import asyncio
from types import SimpleNamespace
import pytest
from sqlalchemy import select,func
from common.models.log_models import ChatMessage
from common.models.knowledge_models import CustomerServiceKnowledge,Product
from common.models.conversation_control_models import ConversationControl
from common.models.message_delivery_models import MessageInbox
from common.services import conversation_control as control, ai_workspace
from agent.round_knowledge import load_round_knowledge,KnowledgeUnavailable
from agent.ai_reply_engine import _run_agent_loop
from agent.llm_client import LLMResponse,ToolCall,ToolFunction
from agent.agent_config import AgentConfig
from engine.message_consumer import MessageConsumer,ShopRuntime
from engine.reply_engine import ShopConfig,ReplyRules
from channel_pdd.pdd_message import Context,ContextType


def test_tool_budget_closes_each_declared_call_without_executing_extra_tools():
    class StrictClient:
        def __init__(self):self.calls=0
        async def chat(self,messages,tool_choice='auto'):
            self.calls+=1
            if self.calls==1:
                return LLMResponse(tool_calls=[ToolCall('one',ToolFunction('tool')),ToolCall('two',ToolFunction('tool'))])
            declared={t['id'] for m in messages for t in m.get('tool_calls',[])}
            answered={m['tool_call_id'] for m in messages if m['role']=='tool'}
            assert declared==answered=={'one','two'} and tool_choice=='none'
            return LLMResponse(content='请人工核实')
    client=StrictClient()
    def forbidden(*a):pytest.fail('cannot execute a tool past budget')
    assert asyncio.run(_run_agent_loop(client,[{'role':'user','content':'询问'}],1,{},[],forbidden))=='请人工核实'
    assert client.calls==2


@pytest.mark.parametrize('failure',['history','knowledge'])
def test_context_failure_keeps_inbox_recoverable_and_sends_nothing(delivery_db,monkeypatch,failure):
    runtime=ShopRuntime(ShopConfig(shop_pk=1,ai_enabled=True,default_reply_content='不能误发的默认回复'),
        ReplyRules(),AgentConfig(ai_enabled=True,api_key='fixture'))
    c=MessageConsumer('shop_1',1,9,runtime_loader=lambda _:runtime,
        chat_history_reader=lambda *a:[],product_context_reader=lambda *a:None,
        log_writer=lambda *a:None)
    c._forward_chat_event=lambda *a:None
    async def forbidden(*a,**k):pytest.fail('unreliable context must not call model or send')
    monkeypatch.setattr(c,'_send_reply',forbidden)
    monkeypatch.setattr('engine.message_consumer.ai_reply_engine.generate_reply',forbidden)
    if failure=='history':
        def unavailable(*a):raise RuntimeError('synthetic storage error')
        monkeypatch.setattr(ai_workspace,'model_history',unavailable)
    else:
        def unavailable(*a):raise KnowledgeUnavailable('synthetic unavailable')
        monkeypatch.setattr('agent.round_knowledge.load_round_knowledge',unavailable)
    out=asyncio.run(c.process(Context(type=ContextType.TEXT,content='商品问题',kwargs={'from_uid':'c','msg_id':'synthetic'})))
    assert not out.replied and out.log_result=='ai_'+('context' if failure=='history' else 'knowledge')+'_unavailable'
    with delivery_db() as s:
        row=s.scalar(select(MessageInbox))
        assert row.state=='pending'


def test_round_knowledge_is_frozen_and_never_reads_foreign_shop(delivery_db):
    with delivery_db.begin() as s:
        s.add_all([CustomerServiceKnowledge(id=1,shop_pk=1,title='材质',content='本店材质为纯棉'),
            CustomerServiceKnowledge(id=2,shop_pk=2,title='材质',content='外店私有依据'),
            Product(id=1,shop_pk=1,goods_id='p',goods_name='棉被',price=2,status=1)])
    pin=load_round_knowledge(1)
    with delivery_db.begin() as s:s.get(CustomerServiceKnowledge,1).content='本店材质已改为真丝'
    old=pin.execute('search_customer_service_knowledge',{'query':'材质','shop_id':2})
    assert '纯棉' in old and '真丝' not in old and '外店' not in old
    proof=pin.evidence()
    assert len(proof['references'])==1 and proof['references'][0]['content']['content']=='本店材质为纯棉'
    assert len(proof['references'][0]['sha256'])==64
    assert '真丝' in load_round_knowledge(1).execute('search_customer_service_knowledge',{'query':'材质'})


def test_supplement_and_draft_restore_without_becoming_published_knowledge(delivery_db):
    with delivery_db.begin() as s:
        s.add(ChatMessage(id=7,shop_pk=1,customer_uid='c',direction='in',content='问题'))
        state=control.supplement(s,1,'c','人工核实：材质为纯棉',expected_revision=0,actor_id=9)
        revision=state['revision']
    with delivery_db.begin() as s:
        saved=ai_workspace.save_draft(s,1,'c',9,revision,7,'请审核的建议',{'references':[]})
    with delivery_db() as s:
        restored=ai_workspace.read(s,1,'c')
        assert restored['control']['mode']=='human' and restored['supplement']=='人工核实：材质为纯棉'
        assert restored['draft']==saved
        assert s.scalar(select(func.count()).select_from(CustomerServiceKnowledge))==0


@pytest.mark.parametrize('change',['supplement','takeover_cycle','new_message','human_reply','owner','disabled'])
def test_late_draft_cannot_replace_new_state(delivery_db,change):
    from common.models.shop_models import Shop
    with delivery_db.begin() as s:
        s.add(ChatMessage(id=7,shop_pk=1,customer_uid='c',direction='in',content='问题'))
        old=control.supplement(s,1,'c','旧补充',expected_revision=0,actor_id=9)['revision']
    with delivery_db.begin() as s:
        if change=='supplement':control.supplement(s,1,'c','新补充',expected_revision=old,actor_id=9)
        elif change=='takeover_cycle':
            state=control.change(s,1,'c','auto',expected_revision=old)
            control.change(s,1,'c','human',expected_revision=state['revision'])
        elif change=='new_message':s.add(ChatMessage(id=8,shop_pk=1,customer_uid='c',direction='in',content='新问题'))
        elif change=='human_reply':s.add(ChatMessage(id=8,shop_pk=1,customer_uid='c',direction='out',content='人工已答'))
        elif change=='owner':s.get(Shop,1).owner_user_id=10
        else:s.get(Shop,1).status=0
    with delivery_db.begin() as s:
        with pytest.raises(control.ControlConflict):ai_workspace.save_draft(s,1,'c',9,old,7,'迟到建议',{'references':[]})
        assert s.scalar(select(ConversationControl)).draft_json is None


def test_sent_draft_is_not_restored_as_unsent_after_refresh(delivery_db):
    with delivery_db.begin() as s:
        s.add(ChatMessage(id=7,shop_pk=1,customer_uid='c',direction='in',content='问题'))
        revision=control.supplement(s,1,'c','依据',expected_revision=0,actor_id=9)['revision']
        ai_workspace.save_draft(s,1,'c',9,revision,7,'建议',{'references':[]})
    with delivery_db.begin() as s:s.add(ChatMessage(id=8,shop_pk=1,customer_uid='c',direction='out',content='建议'))
    with delivery_db() as s:assert ai_workspace.read(s,1,'c')['draft'] is None


def test_scope_change_invalidates_but_does_not_erase_stored_contribution(delivery_db):
    with delivery_db.begin() as s:
        rev=control.supplement(s,1,'c','已接受人工贡献',expected_revision=0,actor_id=9)['revision']
        control.change(s,1,'c','auto',expected_revision=rev)
    with delivery_db() as s:
        row=s.scalar(select(ConversationControl))
        assert '已接受人工贡献' in row.supplement_text
        assert ai_workspace.read(s,1,'c')['supplement']==''
