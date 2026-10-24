"""Images/videos are archived by original consumer then handed to original human control."""
import json
import uuid
from common.models.shop_models import Shop
from sqlalchemy import select,func
from common.models.message_delivery_models import MessageDelivery
from common.services import ai_workspace, conversation_control as control, human_workspace, harness_state
from common.services.message_delivery import current_inbox, assert_lease


def accept(session,pk,uid,message_id,owner):
    shop=session.get(Shop,pk)
    if not shop or shop.status!=1 or shop.owner_user_id!=owner or shop.merged_into_shop_pk is not None:
        raise ValueError('店铺状态已变化')
    handle=current_inbox.get()
    if handle:assert_lease(session,handle,pk,uid)
    status,source_id=ai_workspace.pending_event(session,pk,uid,message_id)
    if status!='current':return None
    control.change(session,pk,uid,'human',reason='顾客发送图片或视频，请人工查看')
    row=ai_workspace.row_for(session,pk,uid)
    value=human_workspace.ensure_batch(session,row)
    ident=str(uuid.uuid5(uuid.NAMESPACE_URL,f'media:{pk}:{uid}:{source_id}'))
    if any(r['id']==ident for r in value.get('requests',[])):
        return control.view(row)
    state=harness_state.read(row)
    issue={'id':ident,'topic':'查看顾客图片或视频','object_ref':'','action':'handoff','status':'waiting_staff',
        'question':'请查看顾客图片或视频；可直接回复，或在 AI 协作中说明内容并继续处理。',
        'reason':'本阶段不进行图片或视频识别','request_id':ident,'related_ids':[],
        'source_message_id':source_id,'origin_message_id':source_id,'source_message_ids':[source_id]}
    issue['media']=True
    issue['after_delivery_id']=session.scalar(select(func.max(MessageDelivery.id)).where(
        MessageDelivery.shop_pk==pk,MessageDelivery.customer_uid==str(uid))) or 0
    state['issues'].append(issue)
    value.setdefault('requests',[]).append({'id':ident,'issue_id':ident,'question':issue['question'],
        'status':'open','source_message_id':source_id})
    value['assessed']=True;row.workspace_json=json.dumps(value,ensure_ascii=False)
    row.harness_json=json.dumps(state,ensure_ascii=False);row.revision+=1;row.draft_json=None
    row.attention_count=control.view(row)['attention_count'];session.flush()
    return control.view(row)


def reconcile_manual(session,row):
    if not row or not row.harness_json:return
    state=harness_state.read(row);value=human_workspace.data(row) or {};changed=False
    for issue in state['issues']:
        if not issue.get('media') or issue.get('status')!='waiting_staff':continue
        delivery=session.scalar(select(MessageDelivery).where(MessageDelivery.shop_pk==row.shop_pk,
            MessageDelivery.customer_uid==row.customer_uid,MessageDelivery.id>issue['after_delivery_id'],
            MessageDelivery.operation_key.like('manual:%'),MessageDelivery.state.in_(['accepted','confirmed']))
            .order_by(MessageDelivery.id).limit(1))
        if not delivery:continue
        issue['status']='answered';issue['action']='resolve'
        issue['answer_source']={'kind':'manual_delivery','id':delivery.id}
        for request in value.get('requests',[]):
            if request['id']==issue['request_id']:request['status']='resolved'
        changed=True
    if changed:
        row.harness_json=json.dumps(state,ensure_ascii=False)
        row.workspace_json=json.dumps(value,ensure_ascii=False)
        row.attention_count=control.view(row)['attention_count']
