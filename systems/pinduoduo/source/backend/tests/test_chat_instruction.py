import pytest
from sqlalchemy import select,func
from common.schemas.common import success_response
from common.services import ai_workspace,conversation_control,operator_commands,message_delivery as ledger
from common.models.message_delivery_models import MessageDelivery
from common.models.log_models import ChatMessage
from app.services import chat_ai_service as ai,chat_instruction_service as command,chat_send_client
from .test_chat_workspace import env
from .test_chat_api import chat_env


@pytest.mark.parametrize('text',['OK，你发给他吧','发给顾客','好的，发送给客户吧','确认发送'])
def test_only_explicit_current_operator_command_authorizes_send(text):
    assert operator_commands.explicitly_send(text)


@pytest.mark.parametrize('text',['不要发给他','先别发给他','你能发给他吗？','如果合适就发给他','这句话是客户说的：发给他','生成一个回复草稿','有货'])
def test_ambiguous_negative_and_quoted_commands_do_not_send(text):
    assert not operator_commands.explicitly_send(text)


def prepare(db_session,env):
    data,user=env;pk=data['shop_a_pk'];uid='C1'
    state=ai.ai_workspace(db_session,user,pk,uid,text='OK，你发给他吧',expected_revision=0,kind='instruction').data
    ident=next(e['id'] for e in state['entries'] if e['kind']=='instruction')
    db_session.commit();return pk,uid,user,ident


def test_send_command_reuses_delivery_and_does_not_regenerate_on_retry(db_session,env,monkeypatch):
    pk,uid,user,ident=prepare(db_session,env);generated=[];posts=[]
    def draft(session,operator,shop,customer,**kwargs):
        generated.append(1);state=conversation_control.read(session,shop,customer)
        result=ai_workspace.save_draft(session,shop,customer,operator.id,state['revision'],
            ai_workspace.latest_inbound(session,shop,customer),'有货的，您可以回店里选购。',{},ai_workspace.latest_message(session,shop,customer))
        session.commit();return success_response(data=result)
    def send(**kwargs):
        row,fresh=ledger.reserve(pk,uid,'manual:'+kwargs['operation_key'],'text',kwargs['content'],revision=kwargs['control_revision'],
            expected_mode='human',history_boundary_id=kwargs['history_boundary_id'])
        if fresh:
            posts.append(1);row=ledger.settle(row['id'],{'success':True,'result':{'msg_id':'fixture-out'}})
        return chat_send_client.ManualSendResult(ok=True,message_id='fixture-out',delivery_state=row['state'])
    monkeypatch.setattr(ai,'draft',draft);monkeypatch.setattr(chat_send_client,'send_manual_message',send)
    first=command.execute(db_session,user,pk,uid,ident);second=command.execute(db_session,user,pk,uid,ident)
    assert first.success and second.success and generated==[1] and posts==[1]
    assert db_session.scalar(select(func.count()).select_from(MessageDelivery))==1


def test_customer_message_after_generation_blocks_old_instruction(db_session,env,monkeypatch):
    pk,uid,user,ident=prepare(db_session,env)
    def draft(session,operator,shop,customer,**kwargs):
        state=conversation_control.read(session,shop,customer)
        result=ai_workspace.save_draft(session,shop,customer,operator.id,state['revision'],
            ai_workspace.latest_inbound(session,shop,customer),'旧草稿',{},ai_workspace.latest_message(session,shop,customer))
        session.add(ChatMessage(shop_pk=shop,customer_uid=customer,direction='in',msg_type='text',content='我改主意了'))
        session.commit();return success_response(data=result)
    monkeypatch.setattr(ai,'draft',draft)
    def forbidden(**kwargs):raise AssertionError('stale draft must not send')
    monkeypatch.setattr(chat_send_client,'send_manual_message',forbidden)
    assert not command.execute(db_session,user,pk,uid,ident).success
    assert db_session.scalar(select(func.count()).select_from(MessageDelivery))==0
