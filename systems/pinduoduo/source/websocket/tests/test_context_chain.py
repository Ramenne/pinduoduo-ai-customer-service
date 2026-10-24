"""原OIN关键不变量贯穿真实存储与执行入口；外部发送为禁止调用的传输边界。"""
import asyncio
import json
from datetime import datetime,timedelta
from types import SimpleNamespace
import pytest
from sqlalchemy import select,func
from common.models.log_models import ChatMessage
from common.models.config_models import LlmConfig
from common.models.knowledge_curation_models import KnowledgeCuration
from common.models.message_delivery_models import MessageInbox,MessageDelivery
from common.services import ai_workspace as ws,harness_state as hs,human_workspace as hw,conversation_control as cc
from common.services.harness_contract import parse
from agent import harness_runtime,knowledge_curation
from engine.message_consumer import MessageConsumer,ShopRuntime
from engine.reply_engine import ShopConfig,ReplyRules
from channel_pdd.pdd_message import Context,ContextType


def source(db,text='规格为五斤装'):
    with db.begin() as s:
        s.add(ChatMessage(id=1,shop_pk=1,customer_uid='c',direction='in',content='有货吗',msg_id='one',msg_time=datetime(2026,10,22,10)))
        cc.supplement(s,1,'c',text,expected_revision=0,actor_id=9)
    return snapshot(db)


def snapshot(db):
    with db.begin() as s:
        snap=hs.context(s,1,'c',kind='operator')
        snap['event']['source_id']=snap['operator_sources'][-1]['id'] if snap['operator_sources'] else None
        return snap


def decision(snap,issues=None,lifetime='session'):
    return parse(json.dumps({'version':'oin-business-1','message':'已整理人工补充','customer_reply':'',
        'issues':issues or [], 'facts':[{'source_id':snap['operator_sources'][-1]['id'],
        'text':'规格为五斤装','object_ref':'goods:1','scope':'该商品','lifetime':lifetime}]}))


def apply(db,snap,d):
    with db.begin() as s:return hs.apply(s,1,'c',9,snap,d,audience='advice')


def test_answered_event_does_not_reopen_old_customer_turn(delivery_db):
    with delivery_db.begin() as s:
        s.add_all([ChatMessage(id=1,shop_pk=1,customer_uid='c',direction='in',content='你好',msg_id='old',msg_time=datetime(2026,10,22,10)),
            ChatMessage(id=2,shop_pk=1,customer_uid='c',direction='out',content='您好',msg_time=datetime(2026,10,22,10,1))])
    with pytest.raises(harness_runtime.AlreadyAnsweredEvent):harness_runtime.prepare(1,'c',0,'old')
    with delivery_db.begin() as s:
        assert ws.pending_event(s,1,'c','old')[0]=='answered'
        with pytest.raises(cc.ControlConflict):ws.pending_event(s,1,'c','missing')


def test_auto_transport_is_not_called_if_new_message_arrives_during_preparation(monkeypatch,delivery_db):
    from channel_pdd.reliable_delivery import dispatch
    runtime=ShopRuntime(ShopConfig(shop_pk=1,default_reply_content='旧答案'),ReplyRules())
    called=[]
    class Sender:
        def send_text(self,*a):called.append('POST');pytest.fail('stale decision must not cross transport')
    def runner(*args,**kwargs):
        assert kwargs['history_boundary_id']
        with delivery_db.begin() as s:
            s.add(ChatMessage(shop_pk=1,customer_uid='c',direction='in',content='我换了商品',msg_id='two',
                msg_time=datetime.now()+timedelta(days=1)))
        return dispatch(*args,**kwargs)
    c=MessageConsumer('shop_1',1,9,runtime_loader=lambda _:runtime,delivery_runner=runner,log_writer=lambda *a:None)
    c._sender=Sender();c._forward_chat_event=lambda *a:None
    out=asyncio.run(c.process(Context(type=ContextType.TEXT,content='原问题',kwargs={'from_uid':'c','msg_id':'one'})))
    assert out.log_result=='superseded_message' and not called
    with delivery_db() as s:
        assert s.scalar(select(func.count()).select_from(MessageDelivery))==0
        assert s.scalar(select(MessageInbox)).state=='done'


@pytest.mark.parametrize('stamp,known',[(1600000000,True),(1600000000000,True),(None,False),('nan',False)])
def test_platform_timestamp_is_preserved_without_fabricating_unknown(delivery_db,stamp,known):
    c=MessageConsumer('shop_1',1,9)
    event=Context(type=ContextType.TEXT,content='来话',kwargs={'from_uid':'c','msg_id':'one','timestamp':stamp})
    c._record_inbound_message(event,'c',event.content)
    with delivery_db() as s:
        row=s.scalar(select(ChatMessage))
        assert (row.msg_time is not None)==known
        if known:assert row.msg_time.year==2020
        assert row.created_at is not None


def test_issue_origin_and_original_card_survive_long_followup(delivery_db):
    snap=source(delivery_db)
    issue={'id':'','topic':'规格','object_ref':'goods:1','action':'resolve'}
    apply(delivery_db,snap,decision(snap,[issue]))
    with delivery_db.begin() as s:
        s.add(ChatMessage(id=2,shop_pk=1,customer_uid='c',direction='in',content='嗯',msg_time=datetime(2026,10,22,10,1)))
    snap=snapshot(delivery_db);issue['id']=snap['issues'][0]['id']
    apply(delivery_db,snap,decision(snap,[issue]))
    with delivery_db.begin() as s:
        for index in range(3,35):s.add(ChatMessage(id=index,shop_pk=1,customer_uid='c',direction='out',content='后续对话',msg_time=datetime(2026,10,22,10)+timedelta(minutes=index)))
    snap=snapshot(delivery_db)
    with delivery_db.begin() as s:
        rows=ws.history_through(s,1,'c',34,anchor_ids=ws.history_anchors(snap['issues']))
        assert 1 in [r.id for r in rows] and snap['issues'][0]['origin_message_id']==1


def test_same_boundary_keeps_card_fields_and_never_reads_later_product(delivery_db):
    with delivery_db.begin() as s:
        s.add_all([ChatMessage(id=1,shop_pk=1,customer_uid='c',direction='in',content='[商品咨询] A',goods_context=json.dumps({'goods_id':'goods-A','goods_spec':'audit-five-jin'}),msg_time=datetime(2026,10,22,10)),
            ChatMessage(id=2,shop_pk=1,customer_uid='c',direction='in',content='还有货吗',msg_time=datetime(2026,10,22,10,1)),
            ChatMessage(id=3,shop_pk=1,customer_uid='c',direction='in',content='[商品咨询] B',goods_context=json.dumps({'goods_id':'goods-B'}),msg_time=datetime(2026,10,22,10,2))])
    text=json.dumps(MessageConsumer('shop_1',1,9)._load_ai_history('c',source_id=2))
    assert 'goods-A' in text and 'audit-five-jin' in text and 'goods-B' not in text


def test_operator_uses_same_card_context_and_broken_card_stops_both_paths(monkeypatch,delivery_db):
    from routes import ai_assist
    with delivery_db.begin() as s:
        s.add_all([ChatMessage(id=1,shop_pk=1,customer_uid='c',direction='in',content='[商品咨询] A',goods_context=json.dumps({'goods_id':'goods-A','goods_spec':'audit-five-jin'}),msg_time=datetime(2026,10,22,10)),
            ChatMessage(id=2,shop_pk=1,customer_uid='c',direction='in',content='还有吗',msg_time=datetime(2026,10,22,10,1)),LlmConfig(shop_pk=1,model_name='fixture')])
        cc.change(s,1,'c','human');rev=ws.row_for(s,1,'c').revision
    monkeypatch.setattr(ai_assist.AgentConfig,'from_llm_config',lambda _:SimpleNamespace(api_key='fixture'))
    payload=ai_assist.DraftRequest(shop_pk=1,owner_user_id=9,customer_uid='c',source_message_id=2,control_revision=rev,history_boundary_id=2,purpose='advice')
    _,_,history,_,_=ai_assist.prepare(payload)
    assert 'audit-five-jin' in json.dumps(history)
    with delivery_db.begin() as s:s.get(ChatMessage,1).goods_context='not-json'
    with pytest.raises(ValueError):ai_assist.prepare(payload)
    from channel_pdd.reliable_delivery import PreparationUnavailable
    with pytest.raises(PreparationUnavailable):MessageConsumer('shop_1',1,9)._load_ai_history('c',source_id=2)


def test_internal_history_is_retained_but_cannot_authorize_new_batch(delivery_db):
    snap=source(delivery_db,'发给顾客吧')
    with delivery_db.begin() as s:
        row=ws.row_for(s,1,'c');old_id=hw.data(row)['entries'][0]['id']
        cc.change(s,1,'c','auto',expected_revision=row.revision)
        cc.change(s,1,'c','human',expected_revision=row.revision)
        shown=ws.read(s,1,'c')['entries']
        assert any(e['id']==old_id and e['archived'] for e in shown)
    snap=snapshot(delivery_db)
    assert old_id not in [e['id'] for e in snap['operator_sources']]


def test_temporary_fact_retained_as_history_after_day_changes(monkeypatch,delivery_db):
    snap=source(delivery_db);apply(delivery_db,snap,decision(snap,lifetime='temporary'))
    assert snapshot(delivery_db)['facts'][0]['confirmed_at']
    tomorrow=datetime.fromisoformat(snap['operator_sources'][0]['at'])+timedelta(days=1)
    monkeypatch.setattr(hs,'now_beijing_naive',lambda:tomorrow)
    snap=snapshot(delivery_db)
    assert not snap['facts'] and len(snap['historical_facts'])==1


def test_fact_only_correction_updates_state_without_new_customer_issue(delivery_db):
    snap=source(delivery_db);apply(delivery_db,snap,decision(snap))
    snap=snapshot(delivery_db)
    assert snap['issues']==[] and snap['facts'][0]['text']=='规格为五斤装'
    with delivery_db.begin() as s:assert ws.read(s,1,'c')['draft'] is None


def test_failed_analysis_instruction_can_be_curated_after_finish(monkeypatch,delivery_db):
    with delivery_db.begin() as s:
        cc.change(s,1,'c','human');row=ws.row_for(s,1,'c')
        hw.append(s,row,'instruction','这款是五斤装，之前你理解错了',9)
        s.flush()
        cc.change(s,1,'c','auto',expected_revision=row.revision)
        task=s.scalar(select(KnowledgeCuration));assert task is not None
        assert '五斤装' in task.snapshot_json
        s.add(LlmConfig(shop_pk=1,model_name='fixture'))
    monkeypatch.setattr(knowledge_curation.AgentConfig,'from_llm_config',lambda _:SimpleNamespace(api_key='fixture'))
    claimed=knowledge_curation.claim();_,sources=knowledge_curation.prepare(*claimed)
    assert sources[0]['type']=='unclassified'
    result=knowledge_curation.parse_candidates(json.dumps([{'title':'商品规格','content':'这款是五斤装','source_id':sources[0]['id']}]),sources)
    assert result[0]['status']=='pending_review' and result[0]['category']=='product'


def test_business_format_and_references_share_one_bounded_correction():
    from agent.ai_reply_engine import generate_reply
    from agent.llm_client import LLMResponse
    class Client:
        tools=[]
        calls=0
        async def chat(self,messages,**kwargs):
            self.calls+=1
            if self.calls==1:
                return LLMResponse(content=json.dumps({'version':'oin-business-1','message':'请核实','customer_reply':'稍等我核实一下','issues':[],'facts':[]}))
            assert kwargs['tool_choice']=='none'
            return LLMResponse(content=json.dumps({'version':'oin-business-1','message':'需要工作人员核实','customer_reply':'',
                'issues':[{'id':'','topic':'库存','object_ref':'goods:1','action':'request_staff','question':'确认当前库存'}],'facts':[]}))
    client=Client()
    from agent.agent_config import AgentConfig
    result=asyncio.run(generate_reply('还有货吗',AgentConfig(ai_enabled=True,api_key='fixture'),client=client,
        business_context={'event':{'kind':'customer'},'operator_sources':[]},audience='customer'))
    assert result.success and client.calls==2 and result.business_decision['issues'][0]['action']=='request_staff'
