"""当前人工发送指令复用草稿、会话代际和原唯一发送账，不让模型决定授权。"""
import json
from sqlalchemy import select
from common.models.message_delivery_models import MessageDelivery
from common.schemas.common import error_response,success_response
from common.services import ai_workspace,human_workspace,operator_commands


def instruction(session,pk,uid,ident,user_id):
    row=ai_workspace.row_for(session,pk,uid)
    value=human_workspace.data(row) or {}
    entry=next((e for e in value.get('entries',[]) if e['id']==ident),None)
    if not entry or entry['kind'] not in ('instruction','fact') or entry.get('actor_id')!=user_id:
        raise ValueError('发送指令不存在或不属于当前工作人员')
    if not operator_commands.current_send(entry['content']):
        return row,value,entry,False
    return row,value,entry,True


def execute(session,user,pk,uid,ident):
    from app.services import chat_ai_service,chat_service
    key='ai-instruction:'+ident
    try:
        row,value,entry,approved=instruction(session,pk,uid,ident,user.id)
        if not approved:return None  # 普通内部问题仍走原建议生成。
        existing=session.scalar(select(MessageDelivery).where(MessageDelivery.shop_pk==pk,
            MessageDelivery.operation_key=='manual:'+key))
        if existing:
            return outcome(existing.state,existing.id)
        humans=[e for e in value.get('entries',[]) if e['kind'] in ('fact','instruction')]
        if not humans or humans[-1]['id']!=ident:
            return error_response(40900,'人工补充已变化，请重新确认后发送')
        if not entry.get('send'):
            session.commit()  # 不跨模型调用持有会话行锁。
            generated=chat_ai_service.draft(session,user,pk,uid,purpose='draft',instruction_id=ident)
            if not generated.success:return generated
            row,value,entry,approved=instruction(session,pk,uid,ident,user.id)
            humans=[e for e in value.get('entries',[]) if e['kind'] in ('fact','instruction')]
            saved=ai_workspace.read(session,pk,uid)['draft']
            if not saved:
                return error_response(40900,'当前仍有必要信息待核实，或本轮无需回复，未发送消息；请查看AI协作')
            if (humans[-1]['id']!=ident or row.mode!='human'
                or saved['control_revision']!=row.revision
                or saved['history_boundary_id']!=ai_workspace.latest_message(session,pk,uid)):
                return error_response(40900,'对话或人工补充已变化，旧草稿未发送')
            entry['send']={'business_round_id':saved.get('business_round_id'),'body':saved['content'],'revision':row.revision,
                'boundary':saved['history_boundary_id'],'operation_key':key}
            row.workspace_json=json.dumps(value,ensure_ascii=False)
            session.commit()  # 固定同一指令的正文；丢响应重试不得另生成新发送内容。
        prepared=dict(entry['send'])
        result=chat_service.send_message_by_uid(session,user,shop_pk=pk,customer_uid=uid,
            content=prepared['body'],operation_key=key,expected_control_revision=prepared['revision'],
            history_boundary_id=prepared['boundary'])
        # 并发重复请求可能先看到dispatching；晚到的HTTP错误不能覆盖已接受的效果。
        effect=session.scalar(select(MessageDelivery).where(MessageDelivery.shop_pk==pk,
            MessageDelivery.operation_key=='manual:'+key).with_for_update().execution_options(populate_existing=True))
        row,value,entry,_=instruction(session,pk,uid,ident,user.id)
        effect_state=effect.state if effect else 'unconfirmed'
        accepted=effect_state in ('accepted','confirmed')
        from common.services import harness_state
        harness_state.execution(session,pk,uid,entry['send'].get('business_round_id'),effect_state,delivery_key='manual:'+key)
        entry['send']['result']=effect_state
        row.workspace_json=json.dumps(value,ensure_ascii=False)
        human_workspace.append(session,row,'delivery',
            ('回复已确认送达。' if effect_state=='confirmed' else '发送成功，平台已接受。') if accepted else '发送尚未确认，请查看发送核对，不要重复发送。',
            actor_id=user.id,operation_key=key,delivery_state=effect_state)
        session.commit()
        if not accepted:return result if not result.success else error_response(40900,'发送回执待核对，请勿重复发送')
        return success_response(data={'execution':{'state':effect_state},
            'message':(result.data or {}).get('message')},message='回复已提交平台')
    except ValueError as exc:
        session.rollback()
        return error_response(40900,str(exc))


def outcome(state,ident):
    if state in ('accepted','confirmed'):
        return success_response(data={'execution':{'state':state,'delivery_id':ident}},message='该指令已提交，不重复发送')
    return error_response(40900,'该指令已有发送结果待核对，不重复发送')
