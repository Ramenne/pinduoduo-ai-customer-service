from datetime import datetime
from types import SimpleNamespace
import pytest
from sqlalchemy import select
from app.services import chat_workspace_service as service
from app.services.chat_sync_client import SyncResult
from common.models.shop_models import Shop
from common.models.log_models import ChatMessage, Conversation
from common.models.chat_workspace_models import ChatSyncCheckpoint
from common.models.user_models import SysUser
from .test_chat_api import chat_env, _login_token, _auth_header

@pytest.fixture
def env(db_session,chat_env,monkeypatch):
    monkeypatch.setattr(service.connection_notify,'query_connected_batch',lambda keys:{})
    return chat_env, db_session.get(SysUser,chat_env['manager_id'])

def test_offline_search_whole_end_day_and_literal_wildcard(db_session,env):
    data,user=env;pk=data['shop_a_pk']
    response=service.conversations(db_session,user,shop_pk=pk,q='第二',start_time='2024-01-01',end_time='2024-01-01')
    assert response.success and response.data['total']==1
    assert service.messages(db_session,user,shop_pk=pk,customer_uid='C1',q='第二',end_time='2024-01-01').data['messages'][0]['content']=='第二条'
    assert service.messages(db_session,user,shop_pk=pk,customer_uid='C1',q='%').data['messages']==[]
    assert not service.messages(db_session,user,shop_pk=pk,customer_uid='C1',start_time='bad').success

def test_keyset_equal_timestamps_and_after_recovery(db_session,env):
    data,user=env;pk=data['shop_a_pk']
    for i in range(8):db_session.add(ChatMessage(shop_pk=pk,customer_uid='C1',content=f'same-{i}',msg_time=datetime(2024,1,1,12),direction='in',msg_id=f'm{i}'))
    db_session.flush()
    page=service.messages(db_session,user,shop_pk=pk,customer_uid='C1',limit=3).data;ids=[]
    while True:
        ids.extend(m['id'] for m in page['messages'])
        if not page['has_more']:break
        page=service.messages(db_session,user,shop_pk=pk,customer_uid='C1',limit=3,before=page['before_cursor']).data
    assert len(ids)==len(set(ids))==10
    cursor=0;recovered=[]
    while True:
        page=service.messages(db_session,user,shop_pk=pk,customer_uid='C1',limit=3,after_id=cursor).data
        recovered.extend(m['id'] for m in page['messages']);cursor=page['next_after_id']
        if not page['has_more']:break
    assert set(recovered)==set(ids) and recovered==sorted(recovered)
    assert not service.messages(db_session,user,shop_pk=pk,customer_uid='C1',before='!!!').success

def test_archive_and_foreign_scope(db_session,env):
    data,user=env
    archived=Shop(shop_id='archived',shop_name='旧店',owner_user_id=user.id,merged_into_shop_pk=data['shop_a_pk'])
    db_session.add(archived);db_session.flush()
    assert service.list_shops(db_session,user).data['total']==1
    for pk in (archived.id,data['shop_b_pk']):
        assert not service.messages(db_session,user,shop_pk=pk,customer_uid='C1').success
        assert not service.conversations(db_session,user,shop_pk=pk).success
        assert not service.sync_page(db_session,user,shop_pk=pk).success
        assert not service.phrases(db_session,user,pk).success

def test_partial_sync_checkpoint_and_disabled_offline_read(db_session,env,monkeypatch):
    data,user=env;pk=data['shop_a_pk']
    monkeypatch.setattr(service.chat_sync_client,'fetch_history',lambda *a,**kw:SyncResult(ok=True,complete=False,has_more=True,next_cursor='older',sync_error='后续分页失败',persisted=1))
    response=service.sync_page(db_session,user,shop_pk=pk,customer_uid='C1').data
    assert not response['complete'] and response['next_cursor']=='older' and response['error']=='后续分页失败'
    monkeypatch.setattr(service.chat_sync_client,'fetch_history',lambda *a,**kw:SyncResult(ok=False,message='授权失效'))
    assert not service.sync_page(db_session,user,shop_pk=pk,customer_uid='C1',cursor='older').success
    checkpoint=db_session.scalar(select(ChatSyncCheckpoint))
    assert checkpoint.next_cursor=='older' and checkpoint.last_error=='授权失效'
    db_session.get(Shop,pk).status=0;db_session.flush()
    assert not service.sync_page(db_session,user,shop_pk=pk).success
    assert len(service.messages(db_session,user,shop_pk=pk,customer_uid='C1').data['messages'])==2

def test_shared_phrases_and_ownership(db_session,env,test_users):
    data,user=env;pk=data['shop_a_pk']
    result=service.save_phrase(db_session,user,shop_pk=pk,title='发货',content='明日发货',shared=True)
    phrase_id=result.data['id']
    assert service.phrases(db_session,user,pk).data['list'][0]['shared']
    stranger=db_session.get(SysUser,test_users['guest']['id'])
    assert not service.save_phrase(db_session,stranger,shop_pk=pk,title='覆盖',content='无',phrase_id=phrase_id).success
    service.save_phrase(db_session,user,shop_pk=pk,title='发货',content='明日发货',phrase_id=phrase_id,enabled=False)
    assert not service.phrases(db_session,user,pk).data['list']

def test_api_permissions_and_readonly_history(client,db_session,env,test_users):
    data,user=env
    for name,allowed in [('manager',True),('guest',False)]:
        person=test_users[name];headers=_auth_header(_login_token(client,person['username'],person['password']))
        response=client.get('/api/v1/chat/workspace/messages',params={'shop_pk':data['shop_a_pk'],'customer_uid':'C1'},headers=headers)
        response=SimpleNamespace(**response.json())
        assert response.success==allowed

def test_uid_send_persists_confirmed_message_and_disabled_rejection(db_session,env,monkeypatch):
    from app.services import chat_service,chat_send_client
    data,user=env;pk=data['shop_a_pk']
    calls=[]
    def send(**kw):
        calls.append(kw)
        return chat_send_client.ManualSendResult(ok=True,message_id='confirmed-platform-id')
    monkeypatch.setattr(chat_send_client,'send_manual_message',send)
    result=chat_service.send_message_by_uid(db_session,user,shop_pk=pk,customer_uid='C1',content='发货说明')
    assert result.success and result.data['message']['msg_id']=='confirmed-platform-id'
    row=db_session.scalar(select(ChatMessage).where(ChatMessage.msg_id=='confirmed-platform-id'))
    assert row.content=='发货说明' and row.direction=='out'
    db_session.get(Shop,pk).status=0;db_session.flush()
    assert not chat_service.send_message_by_uid(db_session,user,shop_pk=pk,customer_uid='C1',content='不可发送').success
    assert len(calls)==1
