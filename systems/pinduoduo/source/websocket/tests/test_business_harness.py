import asyncio
import json
import pytest
from sqlalchemy import select, func
from common.services import harness_state as hs, conversation_control as cc, ai_workspace as ws
from common.services.harness_contract import parse
from common.models.log_models import ChatMessage
from common.models.knowledge_curation_models import KnowledgeCuration
from common.models.conversation_control_models import ConversationControl
from common.services.operator_commands import current_send


def decision(*issues, facts=None, reply=''):
    return parse(json.dumps({'version':'oin-business-1','message':'核对结果','customer_reply':reply,
        'issues':[{'id':'','topic':topic,'object_ref':obj,'action':action,'question':question,
            'reason':'当前依据','request_id':''} for topic,obj,action,question in issues], 'facts':facts or []}))


def start(db, text='有货，物流还需确认'):
    with db.begin() as s:
        s.add(ChatMessage(id=1,shop_pk=1,customer_uid='c',direction='in',content='有货吗，周五能到吗'))
        cc.change(s,1,'c','human')
        cc.supplement(s,1,'c',text,expected_revision=1,actor_id=9)
    return snapshot(db)


def snapshot(db, kind='operator'):
    with db.begin() as s:
        v=hs.context(s,1,'c',kind=kind)
        if v['operator_sources']:v['event']['source_id']=v['operator_sources'][-1]['id']
        return v


def apply(db, snap, d):
    with db.begin() as s:return hs.apply(s,1,'c',9,snap,d,audience='advice')


def fact(snap, text='有货', lifetime='temporary', **extra):
    return {'source_id':snap['operator_sources'][-1]['id'],'text':text,'object_ref':'goods:1',
        'scope':'当前该商品','category':'product' if lifetime=='reusable' else '', 'lifetime':lifetime,'supersedes':'','corrects_knowledge':'',**extra}


def test_partial_answer_keeps_other_issue_and_persists(delivery_db):
    db=delivery_db;snap=start(db)
    result=apply(db,snap,decision(('库存','goods:1','reply',''),('物流','goods:1','request_staff','确认配送承诺'),
        facts=[fact(snap)],reply='有货，您要哪款？'))
    with db.begin() as s:
        row=ws.row_for(s,1,'c');state=hs.read(row)
        assert [i['status'] for i in state['issues']]==['ready','waiting_staff']
        assert ws.read(s,1,'c')['requests'][0]['question']=='确认配送承诺'
        hs.execution(s,1,'c',result['business_round_id'],'accepted')
    with db.begin() as s:
        assert [i['status'] for i in hs.read(ws.row_for(s,1,'c'))['issues']]==['answered','waiting_staff']
        assert s.scalar(select(func.count()).select_from(KnowledgeCuration))==0


def test_one_resolved_does_not_clear_waiting_or_context(delivery_db):
    db=delivery_db;snap=start(db)
    apply(db,snap,decision(('库存','goods:1','resolve',''),('物流','goods:1','request_staff','确认时效')))
    snap=snapshot(db);old=snap['issues'];d=decision(('新规格','goods:2','ask_customer','需要哪个规格'),reply='您想要哪个规格？')
    apply(db,snap,d)
    with db.begin() as s:
        state=hs.read(ws.row_for(s,1,'c'));assert len(state['issues'])==3
        assert state['issues'][0]['status']=='resolved' and state['issues'][1]['status']=='waiting_staff'
        assert len(ws.read(s,1,'c')['requests'])==1


def test_mixed_fact_and_stop_never_creates_draft(delivery_db):
    db=delivery_db;snap=start(db,'暂时没货，我已经回复了，不用再发')
    apply(db,snap,decision(('库存','goods:1','reply',''),facts=[fact(snap,'暂时没货')],reply='现在没货'))
    with db.begin() as s:
        assert ws.read(s,1,'c')['draft'] is None
        assert hs.read(ws.row_for(s,1,'c'))['facts'][0]['text']=='暂时没货'


def test_correction_replaces_only_same_object_and_stays_current(delivery_db):
    db=delivery_db;snap=start(db)
    apply(db,snap,decision(('库存','goods:1','no_reply',''),facts=[fact(snap)]))
    snap=snapshot(db);old=snap['facts'][0]
    with db.begin() as s:cc.supplement(s,1,'c','刚核实这款已没货',expected_revision=snap['revision'],actor_id=9)
    snap=snapshot(db)
    apply(db,snap,decision(('库存','goods:1','no_reply',''),facts=[fact(snap,'没货',supersedes=old['id'])]))
    snap=snapshot(db);assert [f['text'] for f in snap['facts']]==['没货']
    with pytest.raises(ValueError):
        apply(db,snap,decision(('库存','goods:1','no_reply',''),facts=[fact(snap,'没货',object_ref='goods:2',supersedes=old['id'])]))


def test_reusable_internal_contribution_queues_once_without_finish(delivery_db):
    db=delivery_db;snap=start(db,'这款规格为5斤装，常温保存')
    d=decision(('规格','goods:1','no_reply',''),facts=[fact(snap,'这款规格为5斤装，常温保存','reusable')])
    apply(db,snap,d);apply(db,snapshot(db),d)
    with db.begin() as s:
        assert s.scalar(select(func.count()).select_from(KnowledgeCuration))==1
        row=ws.row_for(s,1,'c');cc.change(s,1,'c','auto',expected_revision=row.revision)
        assert s.scalar(select(func.count()).select_from(KnowledgeCuration))==1
        assert hs.read(row)['facts'][0]['text']=='这款规格为5斤装，常温保存'


def test_stale_message_or_actor_cannot_apply(delivery_db):
    db=delivery_db;snap=start(db)
    d=decision(('库存','goods:1','reply',''),reply='有货')
    with db.begin() as s:s.add(ChatMessage(shop_pk=1,customer_uid='c',direction='in',content='换规格了'))
    with pytest.raises(ValueError):apply(db,snap,d)
    snap=snapshot(db)
    with db.begin() as s:
        with pytest.raises(ValueError):hs.apply(s,1,'c',999,snap,d,audience='advice')


def test_unknown_fact_source_rejected_and_no_implicit_send(delivery_db):
    db=delivery_db;snap=start(db)
    with pytest.raises(ValueError):
        apply(db,snap,decision(('库存','goods:1','no_reply',''),facts=[fact(snap,source_id='invented')]))
    assert current_send('有货，发给他吧')
    assert not current_send('有货，但不要发给他')
    assert not current_send('如果有货，发给他吧')
    assert not current_send('顾客说：发给他吧')


def test_auto_entry_persists_decision_without_manual_authorization(delivery_db):
    db=delivery_db
    with db.begin() as s:s.add(ChatMessage(id=1,shop_pk=1,customer_uid='c',direction='in',content='这个数字什么意思'))
    snap=snapshot(db,'customer')
    with db.begin() as s:
        result=hs.apply(s,1,'c',9,snap,decision(('编号','unknown','ask_customer','编号含义'),reply='您发的是订单号吗？'),audience='customer')
        assert ws.row_for(s,1,'c').mode=='auto'
        assert result['control_revision']==2


def test_all_audiences_load_same_policy_and_structured_result():
    from agent.ai_reply_engine import generate_reply
    from agent.agent_config import AgentConfig
    from agent.llm_client import LLMResponse
    class Client:
        tools=[]
        async def chat(self,messages,**kw):
            from agent.harness_policy import POLICY_VERSION
            assert '统一业务指引 '+POLICY_VERSION in messages[0]['content']
            assert '售后指引' in messages[0]['content']
            return LLMResponse(content=json.dumps({'version':'oin-business-1','message':'问顾客规格','customer_reply':'您要哪个规格？',
                'issues':[{'topic':'规格','action':'ask_customer','question':'需要哪款'}],'facts':[]}))
    ctx={'event':{'kind':'customer'},'operator_sources':[]}
    for audience in ('customer','operator','draft'):
        result=asyncio.run(generate_reply('你好，有货吗',AgentConfig(ai_enabled=True,api_key='fixture'),
            client=Client(),business_context=ctx,audience=audience))
        assert result.success and result.business_decision['issues'][0]['action']=='ask_customer'


def test_invalid_business_result_never_falls_back_to_customer_default():
    from agent.ai_reply_engine import generate_reply
    from agent.agent_config import AgentConfig
    from agent.llm_client import LLMResponse
    class Client:
        tools=[]
        async def chat(self,*a,**kw):return LLMResponse(content='我已经发送了')
    r=asyncio.run(generate_reply('问题',AgentConfig(ai_enabled=True,api_key='fixture'),client=Client(),
        default_reply='您好',business_context={'event':{'kind':'customer'}}))
    assert not r.success and r.content is None


def test_waiting_issue_survives_human_batch_close_and_reopen(delivery_db):
    db=delivery_db;snap=start(db)
    apply(db,snap,decision(('物流','goods:1','request_staff','确认物流时效')))
    with db.begin() as s:
        row=ws.row_for(s,1,'c');cc.change(s,1,'c','auto',expected_revision=row.revision)
        cc.change(s,1,'c','human',expected_revision=row.revision)
    snap=snapshot(db)
    d=decision(('物流','goods:1','request_staff','确认物流时效'));d['issues'][0]['id']=snap['issues'][0]['id']
    apply(db,snap,d)
    with db.begin() as s:
        assert len(hs.read(ws.row_for(s,1,'c'))['issues'])==1
        assert len(ws.read(s,1,'c')['requests'])==1


def test_unknown_staff_answer_reuses_one_pending_item(delivery_db):
    db=delivery_db;snap=start(db,'我也不知道物流时效')
    d=decision(('物流','goods:1','request_staff','确认物流时效'))
    apply(db,snap,d);apply(db,snapshot(db),d)
    with db.begin() as s:assert len(ws.read(s,1,'c')['requests'])==1


def test_old_receipt_cannot_mark_new_round_answered(delivery_db):
    db=delivery_db;snap=start(db)
    first=apply(db,snap,decision(('库存','goods:1','reply',''),reply='有货'))
    d=decision(('库存','goods:1','reply',''),reply='新回复')
    d['issues'][0]['id']=snapshot(db)['issues'][0]['id']
    apply(db,snapshot(db),d)
    with db.begin() as s:
        hs.execution(s,1,'c',first['business_round_id'],'confirmed')
        assert hs.read(ws.row_for(s,1,'c'))['issues'][0]['status']=='ready'


def test_unidentified_product_is_scoped_to_source_not_whole_shop(delivery_db):
    db=delivery_db;snap=start(db)
    d=decision(('库存','','no_reply',''),facts=[fact(snap,object_ref='')])
    apply(db,snap,d)
    assert snapshot(db)['facts'][0]['object_ref']=='unresolved:1'


def test_optional_unknown_relation_does_not_reject_real_feedback(delivery_db):
    db=delivery_db;snap=start(db)
    d=decision(('库存','goods:1','no_reply',''),facts=[fact(snap,'刚核实有货')])
    d['issues'][0]['related_ids']=['invented-old-issue']
    result=apply(db,snap,d)
    assert result['reference_warnings']
    with db.begin() as s:
        state=hs.read(ws.row_for(s,1,'c'))
        assert state['facts'][0]['text']=='刚核实有货'
        assert state['issues'][0]['related_ids']==[]
        assert ws.read(s,1,'c')['draft'] is None


def test_same_round_new_issues_can_reference_program_assigned_ids(delivery_db):
    db=delivery_db;snap=start(db)
    d=decision(('库存','goods:1','no_reply',''),('物流','goods:1','request_staff','核实物流'))
    d['issues'][0].update(local_ref='stock',related_ids=['shipping'])
    d['issues'][1].update(local_ref='shipping',related_ids=['stock'])
    apply(db,snap,d)
    with db.begin() as s:
        issues=hs.read(ws.row_for(s,1,'c'))['issues']
        assert issues[0]['related_ids']==[issues[1]['id']]
        assert issues[1]['related_ids']==[issues[0]['id']]


def test_wrong_mutation_target_still_cannot_close_existing_problem(delivery_db):
    db=delivery_db;snap=start(db)
    apply(db,snap,decision(('物流','goods:1','request_staff','核实物流')))
    d=decision(('物流','goods:1','resolve',''));d['issues'][0]['id']='unknown-or-another-shop'
    with pytest.raises(ValueError):apply(db,snapshot(db),d)
    with db.begin() as s:assert hs.read(ws.row_for(s,1,'c'))['issues'][0]['status']=='waiting_staff'


def test_internal_ok_and_reported_sent_do_not_reopen_customer_greeting(delivery_db):
    db=delivery_db;snap=start(db,'ok');snap['event']['purpose']='advice'
    d=hs.operator_event_decision(snap);assert d and d['issues']==[] and d['customer_reply']==''
    apply(db,snap,d)
    with db.begin() as s:
        assert ws.read(s,1,'c')['draft'] is None
        assert hs.read(ws.row_for(s,1,'c'))['issues']==[]
        cc.supplement(s,1,'c','可以发送，并且我已经发送了',expected_revision=ws.row_for(s,1,'c').revision,actor_id=9)
    snap=snapshot(db);snap['event']['purpose']='advice'
    apply(db,snap,hs.operator_event_decision(snap))
    with db.begin() as s:
        state=hs.read(ws.row_for(s,1,'c'))
        assert state['rounds'][-1]['execution']=='operator_reported'
        assert ws.read(s,1,'c')['draft'] is None


def test_generation_includes_actual_outbound_after_last_inbound(delivery_db):
    from datetime import datetime
    db=delivery_db
    with db.begin() as s:
        s.add_all([ChatMessage(id=1,shop_pk=1,customer_uid='c',direction='in',content='你好',msg_time=datetime(2026,10,23,1)),
            ChatMessage(id=2,shop_pk=1,customer_uid='c',direction='out',content='您好，请问有什么需要帮助的',msg_time=datetime(2026,10,23,1,1))])
    with db.begin() as s:
        history=ws.history_through(s,1,'c',2)
        assert [m.direction for m in history]==['in','out']
        with pytest.raises(ValueError):ws.history_through(s,1,'another-customer',2)


def test_invalid_target_is_repaired_once_using_trusted_context():
    from agent.ai_reply_engine import generate_reply
    from agent.agent_config import AgentConfig
    from agent.llm_client import LLMResponse
    correct='52c16e07-89bc-4bc2-9c58-b60f04217440'
    class Client:
        tools=[]
        calls=0
        async def chat(self,messages,**kw):
            self.calls+=1
            if self.calls==2:
                assert kw['tool_choice']=='none' and '业务引用校验未通过' in messages[-1]['content']
            return LLMResponse(content=json.dumps({'version':'oin-business-1','message':'核实物流','customer_reply':'',
                'issues':[{'id':'invented' if self.calls==1 else correct,'topic':'物流','object_ref':'goods:1','action':'request_staff','question':'核实物流'}],'facts':[]}))
    ctx={'event':{'kind':'operator'},'operator_sources':[], 'issues':[{'id':correct,'object_ref':'goods:1'}]}
    client=Client();result=asyncio.run(generate_reply('补充反馈',AgentConfig(ai_enabled=True,api_key='fixture'),
        client=client,business_context=ctx,audience='operator'))
    assert result.success and client.calls==2 and result.business_decision['issues'][0]['id']==correct


def test_still_invalid_target_stops_after_one_repair_without_customer_fallback():
    from agent.ai_reply_engine import generate_reply
    from agent.agent_config import AgentConfig
    from agent.llm_client import LLMResponse
    class Client:
        tools=[]
        calls=0
        async def chat(self,*args,**kwargs):
            self.calls+=1
            return LLMResponse(content=json.dumps({'version':'oin-business-1','message':'关闭','customer_reply':'',
                'issues':[{'id':'not-real','topic':'问题','action':'resolve'}],'facts':[]}))
    client=Client();result=asyncio.run(generate_reply('反馈',AgentConfig(ai_enabled=True,api_key='fixture'),
        client=client,business_context={'event':{'kind':'operator'}},audience='operator',default_reply='您好'))
    assert not result.success and result.content is None and client.calls==2
    assert result.error=='业务引用校验未通过'


def test_related_history_is_kept_beyond_recent_window_and_after_problem_resolved(delivery_db):
    from datetime import datetime,timedelta
    db=delivery_db
    with db.begin() as s:
        for index in range(35):
            s.add(ChatMessage(id=index+1,shop_pk=1,customer_uid='c',direction='in' if index%2==0 else 'out',
                content='原商品规格五斤' if index==0 else '历史消息',msg_time=datetime(2026,10,22)+timedelta(minutes=index)))
    with db.begin() as s:
        rows=ws.history_through(s,1,'c',35,limit=20,anchor_ids=[1])
        assert rows[0].id==1 and rows[-1].id==35 and len(rows)==21


def test_late_old_customer_event_is_not_regarded_as_new_round(delivery_db):
    from agent import harness_runtime
    from datetime import datetime,timedelta
    with delivery_db.begin() as s:
        s.add_all([ChatMessage(id=1,shop_pk=1,customer_uid='c',direction='in',content='你好',msg_id='old',msg_time=datetime(2026,10,22)),
            ChatMessage(id=2,shop_pk=1,customer_uid='c',direction='in',content='问新商品',msg_id='new',msg_time=datetime(2026,10,23))])
    with pytest.raises(harness_runtime.SupersededEvent):harness_runtime.prepare(1,'c',0,'old')
    assert harness_runtime.prepare(1,'c',0,'new')['source_message_id']==2
