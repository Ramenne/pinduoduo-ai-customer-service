"""Whole local chain: archive -> restart -> scoped recall -> original mutation/delivery."""
import json
import time
import uuid
from datetime import datetime,timedelta
import pytest
from sqlalchemy import select,func
from common.services import context_archive as cold,harness_state as hs,ai_workspace as ws,conversation_control as cc,human_workspace as human
from common.models.harness_archive_models import ContextArchive as Archive,HarnessExperience as Experience
from common.models.log_models import ChatMessage
from common.models.shop_models import Shop
from common.models.message_delivery_models import MessageDelivery
from common.services.harness_contract import parse
from agent import context_compression as compression


def start(db):
    with db.begin() as s:
        s.add(ChatMessage(shop_pk=1,customer_uid='c',direction='in',msg_id='m1',content='商品goods:1规格是什么'))
        s.flush();cc.change(s,1,'c','human')
        ws.save_instruction(s,1,'c','你理解错了，是五斤装。',1,9)


def snap(db,source=True,**kw):
    with db.begin() as s:
        row=ws.row_for(s,1,'c');entries=human.data(row)['entries']
        return hs.context(s,1,'c',kind='operator',source_id=entries[-1]['id'] if source and entries else None,**kw)


def decide(**kw):
    return parse(json.dumps({'version':'oin-business-1','message':'已核对','customer_reply':'','issues':[],'facts':[],**kw}))


def issue(n,status='resolved',obj=None):
    return {'id':str(uuid.uuid5(uuid.NAMESPACE_URL,'issue:'+str(n))),'topic':f'规格咨询{n}',
        'object_ref':obj or f'goods:{n}','action':'resolve','status':status,'source_message_id':1,
        'origin_message_id':1,'source_message_ids':[1],'question':'请核实' if status=='waiting_staff' else '',
        'request_id':'','related_ids':[]}


def test_long_state_archives_before_eviction_and_recall_same_id_after_restart(delivery_db):
    db=delivery_db;start(db)
    with db.begin() as s:
        row=ws.row_for(s,1,'c');state=hs.read(row)
        state['issues']=[issue(n) for n in range(160)]+[issue(999,'waiting_staff')]
        state['facts']=[{'id':str(uuid.uuid4()),'text':f'历史商品{n}五斤装','object_ref':f'goods:{n}',
            'active':True,'lifetime':'session'} for n in range(300)]
        row.harness_json=json.dumps(state)
    ctx=snap(db,query='想再问goods:0规格')
    assert any(i['id']==issue(0)['id'] for i in ctx['issues'])
    assert any(i['id']==issue(999)['id'] and i['status']=='waiting_staff' for i in ctx['issues'])
    with db.begin() as s:
        row=ws.row_for(s,1,'c');hot=hs.read(row)
        assert len(hot['issues'])==32 and len(hot['facts'])==64
        assert s.scalar(select(func.count()).select_from(Archive).where(Archive.kind=='state_segment'))>0
        assert len(json.loads(cold.latest(s,1,'c','state_issue',issue(0)['id']).payload_json)['source_message_ids'])==1
    d=decide(issues=[{'id':issue(0)['id'],'topic':'规格追问','object_ref':'goods:0','action':'ask_customer','question':'想要哪个规格'}],customer_reply='您需要哪个规格？')
    with db.begin() as s:
        hs.apply(s,1,'c',9,ctx,d,audience='advice')
    with db.begin() as s:
        actual=next(i for i in hs.read(ws.row_for(s,1,'c'))['issues'] if i['id']==issue(0)['id'])
        assert actual['origin_message_id']==1 and actual['status']=='waiting_customer'
        assert s.scalar(select(func.count()).select_from(MessageDelivery))==0


def test_archive_rollback_keeps_hot_state(delivery_db):
    db=delivery_db;start(db)
    with db.begin() as s:
        row=ws.row_for(s,1,'c');state=hs.read(row);state['issues']=[issue(n) for n in range(80)];row.harness_json=json.dumps(state)
    with db() as s:
        tx=s.begin();hs.context(s,1,'c',kind='operator');tx.rollback()
    with db.begin() as s:
        assert len(hs.read(ws.row_for(s,1,'c'))['issues'])==80
        assert s.scalar(select(func.count()).select_from(Archive))==0


def test_unfinished_items_never_evicted_and_stale_decision_does_not_overwrite(delivery_db):
    db=delivery_db;start(db)
    with db.begin() as s:
        row=ws.row_for(s,1,'c');state=hs.read(row);state['issues']=[issue(n,'waiting_staff') for n in range(60)];row.harness_json=json.dumps(state)
    ctx=snap(db)
    assert len(ctx['issues'])==60
    with db.begin() as s:ws.save_instruction(s,1,'c','又核实了，先不要发送',ctx['revision'],9)
    with pytest.raises(ValueError):
        with db.begin() as s:hs.apply(s,1,'c',9,ctx,decide(),audience='advice')


def test_recalled_old_fact_never_overwrites_newer_hot_correction(delivery_db):
    db=delivery_db;start(db)
    with db.begin() as s:
        cold.put(s,1,'c','state_fact','old',{'id':'old','text':'十斤','active':True,'object_ref':'goods:1','lifetime':'session'})
        cold.put(s,1,'c','state_fact','old',{'id':'old','text':'十斤','active':False,'object_ref':'goods:1','lifetime':'session','replaced_by':'new'})
        row=ws.row_for(s,1,'c');state=hs.read(row);state['facts']=[{'id':'new','text':'不是十斤，是五斤','active':True,'object_ref':'goods:1','lifetime':'session'}];row.harness_json=json.dumps(state)
    ctx=snap(db,query='goods:1十斤')
    assert [f['text'] for f in ctx['facts']]==['不是十斤，是五斤']


def test_old_v1_answer_survives_16_turns_and_selected_correction_stays_v1(delivery_db):
    db=delivery_db;start(db);first=None
    reference={'source':'pdd_product_knowledge','id':7,'version_no':1,'content_hash':'v1hash','content':{'spec':'十斤'}}
    for n in range(21):
        ctx=snap(db)
        with db.begin() as s:
            res=hs.apply(s,1,'c',9,ctx,decide(),audience='advice',evidence={'references':[dict(reference,version_no=1 if n==0 else 2)]})
            if n==0:first=res['business_round_id']
    with db.begin() as s:
        row=ws.row_for(s,1,'c');assert first not in [r['id'] for r in hs.read(row)['rounds']]
        page=cold.trace_page(s,1,'c');assert len(page['items'])==20 and page['before']
        ws.save_instruction(s,1,'c','你当时用的旧版本不对，实际五斤',row.revision,9,answer_id=first)
    ctx=snap(db)
    assert ctx['target_answer']['id']==first
    assert ctx['knowledge_references'][0]['version_no']==1
    d=decide(facts=[{'source_id':ctx['event']['source_id'],'text':'五斤','object_ref':'goods:1','scope':'本商品',
        'lifetime':'reusable','category':'product','corrects_knowledge':'pdd_product_knowledge:7'}])
    with db.begin() as s:
        hs.apply(s,1,'c',9,ctx,d,audience='advice')
        f=hs.read(ws.row_for(s,1,'c'))['facts'][-1]
        assert f['corrected_reference']['version_no']==1


def test_trace_reads_actual_original_delivery_after_hot_round_evicted(delivery_db):
    db=delivery_db;start(db)
    with db.begin() as s:
        row=cold.put(s,1,'c','answer','early',{'id':'early','customer_reply':'五斤装'},inbox_id=123)
        s.add(MessageDelivery(shop_pk=1,customer_uid='c',operation_key='auto:123',inbox_id=123,
            kind='text',body_hash='h',body=json.dumps('五斤装'),state='accepted',request_id=str(uuid.uuid4()),due_at=0))
        s.flush();hs.execution(s,1,'c','early','accepted',delivery_key='auto:123')
        result=cold.trace_page(s,1,'c')['items'][0]
        assert result['actual_delivery']['state']=='accepted' and result['actual_delivery']['body']=='五斤装'


def test_long_human_batch_rotates_to_cold_and_old_commands_are_not_current_sources(delivery_db):
    db=delivery_db;start(db)
    with db.begin() as s:
        row=ws.row_for(s,1,'c')
        for n in range(100):human.append(s,row,'instruction','请发送给顾客' if n==0 else f'人工补充第{n}条')
        assert len(human.data(row)['entries'])<40
        assert s.scalar(select(func.count()).select_from(Archive).where(Archive.kind=='human_segment'))>=2
        page=cold.history_page(s,1,'c',limit=1)
        assert page['before'] and page['entries']
    ctx=snap(db)
    assert not any(x['text']=='请发送给顾客' for x in ctx['operator_sources'])
    with db.begin() as s:
        row=ws.row_for(s,1,'c');cc.change(s,1,'c','auto',expected_revision=row.revision)
        cc.change(s,1,'c','human',expected_revision=row.revision)
    assert snap(db)['operator_sources']==[]


def test_archive_scope_and_selected_answer_cannot_cross_shop_or_customer(delivery_db):
    db=delivery_db;start(db)
    with db.begin() as s:
        s.add(Shop(id=2,shop_id='other',owner_user_id=10,status=1));s.flush()
        cold.put(s,2,'c','answer','other',{'id':'other','customer_reply':'秘密'})
        cold.put(s,1,'different','answer','other-customer',{'id':'other-customer','customer_reply':'秘密'})
        row=ws.row_for(s,1,'c')
        for ident in ('other','other-customer'):
            with pytest.raises(ValueError):ws.save_instruction(s,1,'c','反馈',row.revision,9,answer_id=ident)
        assert cold.trace_page(s,1,'c')['items']==[]


def test_summary_lease_retry_restart_and_extractive_validation(delivery_db):
    db=delivery_db
    with db.begin() as s:
        r=cold.put(s,1,'c','human_segment','seg',{'entries':[{'id':'one','content':'不是十斤，是五斤；库存未知'}]},summarize=True);ident=r.id
    first=compression.claim();assert first[0]==ident
    with db.begin() as s:s.get(Archive,ident).due_at=0
    second=compression.claim();assert first[1]!=second[1]
    assert not compression.settle(*first,summary={'source_hash':'bad'})
    assert compression.settle(*second,error='TimeoutError')
    with db.begin() as s:s.get(Archive,ident).due_at=0
    third=compression.claim();compression.settle(*third,error='TimeoutError')
    with db.begin() as s:
        row=s.get(Archive,ident);assert row.state=='failed' and '库存未知' in row.payload_json
    assert compression.claim() is None
    sources=[{'id':'one','text':'不是十斤，是五斤；库存未知'}]
    with pytest.raises(ValueError):compression.parse('{"excerpts":[{"id":"one","quote":"库存充足"}]}',sources,'hash')
    value=compression.parse('{"excerpts":[{"id":"one","quote":"不是十斤，是五斤；库存未知"}]}',sources,'hash')
    assert value['coverage_ids']==['one']


def test_experience_natural_feedback_candidate_review_loading_and_revoke(delivery_db):
    from common.services import harness_experience as exp
    db=delivery_db;start(db);ctx=snap(db)
    d=decide(observations=[{'source_id':ctx['event']['source_id'],'category':'interpretation','title':'误读旧招呼',
        'observation':'把已答招呼视作新来话，原因待核','guidance':'先核对客服外发，内部确认不是顾客新消息','trigger_terms':'招呼,你好'}])
    with db.begin() as s:
        hs.apply(s,1,'c',9,ctx,d,audience='advice');r=s.scalar(select(Experience));ident=r.id
        assert exp.load(s,1,'你好')==[]
        exp.review(s,1,ident,1,'publish',title=r.title,guidance=r.guidance,trigger_terms=r.trigger_terms,validation_note='已验证旧招呼后有外发，不重开',actor_id=9)
        assert exp.load(s,1,'你好')[0]['version']==2
        with pytest.raises(ValueError):exp.review(s,1,ident,1,'publish',title=r.title,guidance=r.guidance,trigger_terms=r.trigger_terms,validation_note='验证',actor_id=9)
    ctx=snap(db,query='你好')
    assert ctx['runtime_guidance'][0]['id']==ident
    with db.begin() as s:
        hs.apply(s,1,'c',9,ctx,decide(),audience='advice')
        assert cold.trace_page(s,1,'c')['items'][0]['runtime_guidance']==[{'id':ident,'version':2}]
        r=s.get(Experience,ident)
        exp.review(s,1,ident,2,'revoke',title=r.title,guidance=r.guidance,trigger_terms=r.trigger_terms,validation_note='撤回观察',actor_id=9)
        assert exp.load(s,1,'你好')==[]
        assert s.scalar(select(func.count()).select_from(Archive).where(Archive.kind=='experience_version'))==3


def test_duplicate_media_uses_original_control_one_issue_no_send(delivery_db):
    from common.services.media_handoff import accept
    db=delivery_db
    with db.begin() as s:
        s.add(ChatMessage(shop_pk=1,customer_uid='c',direction='in',msg_id='img1',msg_type='image',content='https://example.com/photo.jpg'));s.flush()
        a=accept(s,1,'c','img1',9);b=accept(s,1,'c','img1',9)
        assert a==b and a['mode']=='human' and a['attention_count']==1
    with db.begin() as s:
        row=ws.row_for(s,1,'c');assert len(hs.read(row)['issues'])==1 and len(human.data(row)['requests'])==1
        assert s.scalar(select(func.count()).select_from(MessageDelivery))==0
        cc.supplement(s,1,'c','图片里是规格标签，五斤装',expected_revision=row.revision,actor_id=9)
    ctx=snap(db)
    i=ctx['issues'][0];d=decide(issues=[{**i,'action':'resolve'}])
    with db.begin() as s:
        hs.apply(s,1,'c',9,ctx,d,audience='advice')
        row=ws.row_for(s,1,'c');assert cc.view(row)['attention_count']==0
        cc.change(s,1,'c','auto',expected_revision=row.revision)


def test_old_answered_image_does_not_reopen_human_control(delivery_db):
    from common.services.media_handoff import accept
    db=delivery_db
    with db.begin() as s:
        s.add(ChatMessage(shop_pk=1,customer_uid='c',direction='in',msg_id='img1',msg_type='image',content='url',msg_time=datetime(2026,10,22)))
        s.add(ChatMessage(shop_pk=1,customer_uid='c',direction='out',msg_id='out1',content='已经处理',msg_time=datetime(2026,10,23)));s.flush()
        assert accept(s,1,'c','img1',9) is None
        assert cc.read(s,1,'c')['mode']=='auto'


def test_revoked_guidance_fences_inflight_decision(delivery_db):
    from common.services import harness_experience as exp
    db=delivery_db;start(db)
    with db.begin() as s:
        e=exp.capture(s,1,'c','inflight','interpretation','测试','观察',guidance='核对旧消息',trigger_terms='规格')
        ident=e.id
        exp.review(s,1,ident,1,'publish',title=e.title,guidance=e.guidance,trigger_terms=e.trigger_terms,validation_note='合成验证',actor_id=9)
    ctx=snap(db,query='规格')
    assert ctx['runtime_guidance']
    with db.begin() as s:
        e=s.get(Experience,ident)
        exp.review(s,1,ident,2,'revoke',title=e.title,guidance=e.guidance,trigger_terms=e.trigger_terms,validation_note='撤回',actor_id=9)
    with pytest.raises(ValueError):
        with db.begin() as s:hs.apply(s,1,'c',9,ctx,decide(),audience='advice')


def test_short_conversation_does_not_schedule_summary_per_message(delivery_db):
    db=delivery_db;start(db)
    with db.begin() as s:
        row=ws.row_for(s,1,'c');state=hs.read(row);state['issues']=[issue(n) for n in range(33)]
        compacted=cold.compact(s,row,state)
        assert len(compacted['issues'])==33
        assert s.scalar(select(func.count()).select_from(Archive))==0
        cc.change(s,1,'c','auto',expected_revision=row.revision)
        cc.change(s,1,'c','human',expected_revision=row.revision)
        assert s.scalar(select(func.count()).select_from(Archive).where(Archive.state=='pending'))==0


def test_large_legacy_human_archives_are_split_and_imported_once(delivery_db):
    db=delivery_db;start(db)
    with db.begin() as s:
        row=ws.row_for(s,1,'c');value=human.data(row)
        value['archives']=[{'batch_key':'legacy-large','closed':True,'entries':[human.entry('instruction',f'旧协作内容{n}') for n in range(80)]}]
        row.workspace_json=json.dumps(value)
    snap(db)
    with db.begin() as s:
        row=ws.row_for(s,1,'c');assert human.data(row)['archives']==[]
        rows=list(s.scalars(select(Archive).where(Archive.kind=='human_segment')))
        assert sum(len(json.loads(r.payload_json)['entries']) for r in rows)==80
        assert all(len(json.loads(r.payload_json)['entries'])<=24 for r in rows)
        count=s.scalar(select(func.count()).select_from(Archive))
    snap(db)
    with db.begin() as s:assert s.scalar(select(func.count()).select_from(Archive))==count


def test_preserved_legacy_round_does_not_invent_unknown_versions(delivery_db):
    db=delivery_db;start(db)
    with db.begin() as s:
        row=ws.row_for(s,1,'c');state=hs.read(row)
        state['rounds']=[{'id':'legacy-round','execution':'not_sent','source_message_id':1,'knowledge':{'references':[{'source':'pdd_product_knowledge','id':1,'version_no':None,'content_hash':'old','content':'五斤'}]}}]
        row.harness_json=json.dumps(state)
    snap(db)
    with db.begin() as s:
        item=cold.trace_page(s,1,'c')['items'][0]
        assert item['legacy_import'] and not item['customer_reply']
        assert item['knowledge']['references'][0]['version_no'] is None
        assert item['actual_delivery'] is None


def test_live_media_consumer_persists_url_and_never_calls_model_or_platform(delivery_db,monkeypatch):
    import asyncio
    from engine.message_consumer import MessageConsumer
    from channel_pdd.pdd_message import Context,ContextType
    from common.models.message_delivery_models import MessageInbox
    async def forbidden(*a,**k):pytest.fail('media handoff must not call model, send or platform transfer')
    consumer=MessageConsumer('shop_1',1,9,runtime_loader=lambda _:pytest.fail('media bypasses auto decision'),log_writer=lambda *a:None)
    consumer._forward_chat_event=lambda *a:None
    async def local_event(*a):pass
    consumer._control_event_sender=local_event
    monkeypatch.setattr(consumer,'_send_reply',forbidden)
    monkeypatch.setattr('engine.message_consumer.ai_reply_engine.generate_reply',forbidden)
    ctx=Context(type=ContextType.IMAGE,content={'url':'https://example.invalid/synthetic.jpg'},kwargs={'from_uid':'c','from_user':'user','msg_id':'live-img'})
    first=asyncio.run(consumer.consume_context(ctx));second=asyncio.run(consumer.consume_context(ctx))
    assert first.log_result=='media_human_handoff' and second.log_result=='duplicate_or_pending'
    with delivery_db.begin() as s:
        row=s.scalar(select(ChatMessage));assert json.loads(row.content)['url']=='https://example.invalid/synthetic.jpg'
        inbox=s.scalar(select(MessageInbox));assert inbox.state=='done'
        assert cc.read(s,1,'c')['mode']=='human'
    from channel_pdd.api.get_chat_history import GetChatHistory
    assert json.loads(GetChatHistory._resolve_content(ctx))['url']=='https://example.invalid/synthetic.jpg'


def test_accepted_direct_human_reply_closes_media_before_resume(delivery_db):
    from common.services.media_handoff import accept
    db=delivery_db
    with db.begin() as s:
        s.add(ChatMessage(shop_pk=1,customer_uid='c',direction='in',msg_id='image',msg_type='image',content='url'));s.flush()
        accept(s,1,'c','image',9)
        s.add(MessageDelivery(shop_pk=1,customer_uid='c',operation_key='manual:picture-reply',kind='text',body_hash='h',body=json.dumps('看到了，这是五斤装'),state='accepted',request_id=str(uuid.uuid4()),due_at=0));s.flush()
        row=ws.row_for(s,1,'c');cc.change(s,1,'c','auto',expected_revision=row.revision)
    with db.begin() as s:
        ctx=hs.context(s,1,'c',kind='customer');assert ctx['issues'][0]['status']=='answered'
        assert ctx['issues'][0]['answer_source']['kind']=='manual_delivery'
        assert not any(r['status']=='open' for r in ctx['requests'])


def test_retrieval_reads_matching_early_human_entry_not_only_batch_tail(delivery_db):
    db=delivery_db;start(db)
    with db.begin() as s:
        entries=[{'id':'early','kind':'fact','content':'goods:987 的包装是五斤，不能按十斤介绍','at':'2026-10-22T10:00:00'}]
        entries += [{'id':str(n),'kind':'instruction','content':'其他无关讨论'+str(n)} for n in range(20)]
        cold.put(s,1,'c','human_segment','early-segment',{'batch_key':'old','entries':entries})
        recalled,nav,_=cold.recall(s,1,'c','goods:987 是五斤还是十斤',hs.read(ws.row_for(s,1,'c')))
        assert any(e['id']=='early' for item in nav for e in item.get('entries',[]))
        assert len(nav[0]['entries'])<=8


def test_recall_matches_text_after_cold_navigation_prefix_budget(delivery_db):
    db=delivery_db;start(db)
    with db.begin() as s:
        entries=[{'id':str(n),'kind':'instruction','content':'无关背景'*600} for n in range(10)]
        entries.append({'id':'late','kind':'fact','content':'goods:87654 是五斤装'})
        cold.put(s,1,'c','human_segment','long-raw',{'batch_key':'old','entries':entries})
        _,nav,_=cold.recall(s,1,'c','goods:87654 规格',hs.read(ws.row_for(s,1,'c')))
        assert any(e['id']=='late' for item in nav for e in item.get('entries',[]))


def test_matching_human_evidence_is_kept_when_whole_page_exceeds_read_budget(delivery_db):
    db=delivery_db;start(db)
    with db.begin() as s:
        entries=[{'id':'match','kind':'fact','content':'goods:765 包装五斤 '+('精确事实'*900)}]
        entries += [{'id':str(n),'kind':'instruction','content':'背景讨论'*900} for n in range(9)]
        cold.put(s,1,'c','human_segment','large-page',{'batch_key':'old','entries':entries})
        _,nav,_=cold.recall(s,1,'c','goods:765 包装',hs.read(ws.row_for(s,1,'c')))
        assert any(e['id']=='match' for item in nav for e in item.get('entries',[]))
        assert sum(len(cold.encode(item)) for item in nav)<=22000
