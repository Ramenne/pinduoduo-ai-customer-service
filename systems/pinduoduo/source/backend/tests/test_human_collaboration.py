"""人工事实、内部指令、草稿、结束提炼是不同效果。"""
import json
import pytest
from sqlalchemy import select
from app.services import chat_ai_service as ai
from common.models.knowledge_curation_models import KnowledgeCuration
from common.models.log_models import ChatMessage
from common.models.message_delivery_models import MessageDelivery
from common.services import conversation_control as control, ai_workspace
from .test_chat_workspace import env
from .test_chat_api import chat_env


def test_internal_instruction_is_persistent_but_not_a_fact(db_session, env):
    data, user = env; pk = data['shop_a_pk']
    saved = ai.ai_workspace(db_session,user,pk,'C1',text='帮我检查还缺什么信息',expected_revision=0,kind='instruction')
    assert saved.success and saved.data['entries'][-1]['kind'] == 'instruction'
    assert saved.data['supplement'] == ''
    db_session.commit()
    state = saved.data['control']
    saved = ai.ai_workspace(db_session,user,pk,'C1',text='规格是250毫升',expected_revision=state['revision'])
    assert saved.success and saved.data['supplement']=='规格是250毫升'
    state = saved.data['control']
    saved = ai.ai_workspace(db_session,user,pk,'C1',text='材质是玻璃',expected_revision=state['revision'])
    assert '规格是250毫升' in saved.data['supplement'] and '材质是玻璃' in saved.data['supplement']
    assert len(saved.data['entries']) == 3
    assert not ai.ai_workspace(db_session,user,pk,'C1',text='旧页面',expected_revision=0,kind='instruction').success


def test_finish_once_keeps_unanalyzed_human_sources_and_preserves_history(db_session, env):
    data, user = env; pk = data['shop_a_pk']
    state = ai.ai_workspace(db_session,user,pk,'C1',text='材质是纯棉',expected_revision=0).data['control']
    state = ai.ai_workspace(db_session,user,pk,'C1',text='用礼貌的口吻回答',expected_revision=state['revision'],kind='instruction').data['control']
    ended = ai.conversation_control(db_session,user,pk,'C1','auto',state['revision'])
    assert ended.success
    tasks = list(db_session.scalars(select(KnowledgeCuration)))
    assert len(tasks)==1 and tasks[0].state=='pending'
    snapshot = json.loads(tasks[0].snapshot_json)
    assert [c['content'] for c in snapshot['contributions']]==['材质是纯棉','用礼貌的口吻回答']
    assert all(c['kind'] in ('fact','instruction') for c in snapshot['contributions'])
    assert ai.conversation_control(db_session,user,pk,'C1','auto',ended.data['revision']).success
    assert len(list(db_session.scalars(select(KnowledgeCuration))))==1
    restarted = ai.ai_workspace(db_session,user,pk,'C1',text='规格是M码',expected_revision=ended.data['revision'])
    assert restarted.data['supplement']=='规格是M码'
    assert len(restarted.data['entries'])==3
    assert all(e.get('archived') for e in restarted.data['entries'][:2])
    assert not restarted.data['entries'][-1].get('archived')


def test_no_knowledge_task_for_instruction_only(db_session, env):
    data,user=env;pk=data['shop_a_pk']
    state=ai.ai_workspace(db_session,user,pk,'C1',text='请重新分析',expected_revision=0,kind='instruction').data['control']
    assert ai.conversation_control(db_session,user,pk,'C1','auto',state['revision']).success
    assert db_session.scalar(select(KnowledgeCuration)) is None


def test_advice_is_internal_and_new_message_fences_result(db_session,env):
    data,user=env;pk=data['shop_a_pk']
    state=ai.ai_workspace(db_session,user,pk,'C1',text='材质是纯棉',expected_revision=0).data['control']
    source=ai_workspace.latest_inbound(db_session,pk,'C1'); boundary=ai_workspace.latest_message(db_session,pk,'C1')
    draft=ai_workspace.save_draft(db_session,pk,'C1',user.id,state['revision'],source,'对客草稿',{},boundary)
    ai_workspace.save_advice(db_session,pk,'C1',user.id,draft['control_revision'],source,boundary,'还需要你核实尺寸')
    saved=ai_workspace.read(db_session,pk,'C1')
    assert saved['draft'] is None and saved['entries'][-1]['kind']=='assistant'
    assert saved['control']['revision'] > state['revision']
    db_session.add(ChatMessage(shop_pk=pk,customer_uid='C1',direction='in',content='新问题'))
    db_session.flush()
    with pytest.raises(control.ControlConflict):
        ai_workspace.save_advice(db_session,pk,'C1',user.id,saved['control']['revision'],source,boundary,'迟到建议')
    assert ai_workspace.read(db_session,pk,'C1')['draft'] is None


def test_finish_includes_only_this_batch_manual_deliveries(db_session,env):
    data,user=env;pk=data['shop_a_pk']
    def delivery(key, state):
        d=MessageDelivery(shop_pk=pk,customer_uid='C1',operation_key='manual:'+key,kind='text',
            body_hash='x'*64,body=json.dumps('材质是纯棉'),state=state,request_id=key,due_at=0)
        db_session.add(d);db_session.flush();return d
    old=delivery('old','confirmed')
    state=ai.conversation_control(db_session,user,pk,'C1','human',0).data
    current=delivery('new','accepted')
    assert ai.conversation_control(db_session,user,pk,'C1','auto',state['revision']).success
    task=db_session.scalar(select(KnowledgeCuration))
    assert json.loads(task.snapshot_json)['delivery_ids']==[current.id]


def test_attention_survives_read_and_partial_supply_until_ai_resolves(db_session,env):
    from app.services import chat_workspace_service
    data,user=env;pk=data['shop_a_pk']
    state=ai.ai_workspace(db_session,user,pk,'C1',text='帮我核实客户问题',expected_revision=0,kind='instruction').data['control']
    source=ai_workspace.latest_inbound(db_session,pk,'C1');boundary=ai_workspace.latest_message(db_session,pk,'C1')
    ai_workspace.save_advice(db_session,pk,'C1',user.id,state['revision'],source,boundary,'需要两项信息',
        [{'id':None,'question':'商品材质是什么？'},{'id':None,'question':'规格是什么？'}],[])
    saved=ai_workspace.read(db_session,pk,'C1');ids=[r['id'] for r in saved['requests']]
    assert saved['control']['attention_count']==2 and chat_workspace_service.attention(db_session,user).data['total']==2
    chat_workspace_service.read_conversation(db_session,user,pk,'C1')
    assert chat_workspace_service.attention(db_session,user).data['total']==2
    supplied=ai.ai_workspace(db_session,user,pk,'C1',text='材质是纯棉',expected_revision=saved['control']['revision'])
    assert supplied.data['control']['attention_count']==2
    ai_workspace.save_advice(db_session,pk,'C1',user.id,supplied.data['control']['revision'],source,boundary,
        '材质已确认，还需要规格',[{'id':ids[1],'question':'请继续提供规格'}],[ids[0]])
    saved=ai_workspace.read(db_session,pk,'C1')
    assert saved['control']['attention_count']==1 and chat_workspace_service.attention(db_session,user).data['total']==1
    ai_workspace.save_advice(db_session,pk,'C1',user.id,saved['control']['revision'],source,boundary,'信息足够',[],[ids[1]])
    assert chat_workspace_service.attention(db_session,user).data['total']==0


def test_unknown_request_cannot_be_silently_closed(db_session,env):
    data,user=env;pk=data['shop_a_pk']
    state=ai.conversation_control(db_session,user,pk,'C1','human',0).data
    source=ai_workspace.latest_inbound(db_session,pk,'C1');boundary=ai_workspace.latest_message(db_session,pk,'C1')
    with pytest.raises(control.ControlConflict):
        ai_workspace.save_advice(db_session,pk,'C1',user.id,state['revision'],source,boundary,'都完成了',[],['invented'])


def test_wrong_customer_question_is_withdrawn_without_claiming_answer(db_session,env):
    data,user=env;pk=data['shop_a_pk']
    state=ai.conversation_control(db_session,user,pk,'C1','human',0).data
    source=ai_workspace.latest_inbound(db_session,pk,'C1');boundary=ai_workspace.latest_message(db_session,pk,'C1')
    ai_workspace.save_advice(db_session,pk,'C1',user.id,state['revision'],source,boundary,'旧版错误要求',
        [{'id':None,'question':'顾客想买多少？'}],[])
    before=ai_workspace.read(db_session,pk,'C1');request_id=before['requests'][0]['id']
    ai_workspace.save_advice(db_session,pk,'C1',user.id,before['control']['revision'],source,boundary,
        '这应向顾客询问，不需要你代答。',[],[],withdrawn=[request_id])
    after=ai_workspace.read(db_session,pk,'C1')
    assert after['requests'][0]['status']=='withdrawn' and after['control']['attention_count']==0
    with pytest.raises(control.ControlConflict):
        ai_workspace.save_advice(db_session,pk,'C1',user.id,after['control']['revision'],source,boundary,
            '冲突状态',[],[request_id],withdrawn=[request_id])


def test_old_unstructured_draft_is_kept_for_audit_but_not_restored(db_session,env):
    from common.models.conversation_control_models import ConversationControl
    data,user=env;pk=data['shop_a_pk']
    state=ai.conversation_control(db_session,user,pk,'C1','human',0).data
    source=ai_workspace.latest_inbound(db_session,pk,'C1');boundary=ai_workspace.latest_message(db_session,pk,'C1')
    row=db_session.scalar(select(ConversationControl).where(ConversationControl.shop_pk==pk))
    row.draft_json=json.dumps({'content':'旧版正文及内部说明','control_revision':state['revision'],
        'source_message_id':source,'history_boundary_id':boundary})
    db_session.flush()
    assert ai_workspace.read(db_session,pk,'C1')['draft'] is None
    assert json.loads(row.draft_json)['content'] == '旧版正文及内部说明'


def test_history_backfill_and_notices_do_not_cancel_current_draft(db_session,env):
    from datetime import datetime, timedelta
    data,user=env;pk=data['shop_a_pk']
    current=ChatMessage(shop_pk=pk,customer_uid='C1',direction='in',content='当前问题',msg_time=datetime(2030,1,2))
    db_session.add(current);db_session.flush()
    state=ai.conversation_control(db_session,user,pk,'C1','human',0).data
    boundary=ai_workspace.latest_message(db_session,pk,'C1')
    for kind in ['system_status','auth','mall_system_msg']:
        db_session.add(ChatMessage(shop_pk=pk,customer_uid='C1',direction='in',msg_type=kind,
            content='平台通知',msg_time=datetime(2030,1,3)))
    old=ChatMessage(shop_pk=pk,customer_uid='C1',direction='in',content='补录的旧问题',msg_time=datetime(2030,1,1))
    db_session.add(old);db_session.flush()
    assert old.id > current.id
    assert ai_workspace.latest_inbound(db_session,pk,'C1') == current.id
    assert ai_workspace.latest_message(db_session,pk,'C1') == boundary
    assert old in ai_workspace.history_before(db_session,current)
    saved=ai_workspace.save_draft(db_session,pk,'C1',user.id,state['revision'],current.id,'有效草稿',{},boundary)
    assert ai_workspace.read(db_session,pk,'C1')['draft'] == saved
    # 同代际另一个窗口迟到，不得覆盖先成功的草稿。
    with pytest.raises(control.ControlConflict):
        ai_workspace.save_draft(db_session,pk,'C1',user.id,state['revision'],current.id,'迟到草稿',{},boundary)
    assert ai_workspace.read(db_session,pk,'C1')['draft']['content']=='有效草稿'
    # 真正新增对客回复仍会使旧草稿失效。
    db_session.add(ChatMessage(shop_pk=pk,customer_uid='C1',direction='out',content='人工已回复',msg_time=datetime(2030,1,4)))
    db_session.flush()
    assert ai_workspace.read(db_session,pk,'C1')['draft'] is None
    with pytest.raises(control.ControlConflict):
        ai_workspace.save_draft(db_session,pk,'C1',user.id,saved['control_revision'],current.id,'过期草稿',{},boundary)


def test_missing_platform_time_uses_created_at(db_session,env):
    data,user=env;pk=data['shop_a_pk']
    current=ChatMessage(shop_pk=pk,customer_uid='C1',direction='in',content='无平台时间的新问题')
    db_session.add(current);db_session.flush()
    assert ai_workspace.latest_inbound(db_session,pk,'C1')==current.id
