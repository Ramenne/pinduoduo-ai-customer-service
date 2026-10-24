import json
from app.services import harness_operations as ops
from common.services import context_archive as cold,harness_experience as exp
from common.models.harness_archive_models import ContextArchive
from .test_chat_workspace import env
from .test_chat_api import chat_env,_login_token,_auth_header


def test_operations_scope_versions_and_archive_are_local(db_session,env,monkeypatch):
    data,user=env;pk=data['shop_a_pk']
    # No platform calls are required by these services.
    assert ops.overview(db_session,user,pk).success
    assert not ops.overview(db_session,user,data['shop_b_pk']).success
    with db_session.begin_nested():
        r=cold.put(db_session,pk,'C1','answer','one',{'id':'one','customer_reply':'合成回答'})
        e=exp.capture(db_session,pk,'C1','human:one','interpretation','合成经验','旧消息误判',source={'entry_id':'one'})
        ident=e.id
    assert ops.archive_page(db_session,user,pk,'C1','answer').data['items'][0]['id']=='one'
    assert not ops.archive_page(db_session,user,data['shop_b_pk'],'C1','answer').success
    assert ops.experiences(db_session,user,pk).data['items'][0]['state']=='candidate'
    assert ops.review(db_session,user,pk,ident=ident,expected_version=1,action='publish',title='验证',guidance='处理指引',trigger_terms='旧消息',validation_note='已验证').success
    assert len(ops.versions(db_session,user,pk,ident).data)==2
    assert not ops.review(db_session,user,pk,ident=ident,expected_version=1,action='revoke',title='验证',guidance='',trigger_terms='',validation_note='').success


def test_failed_summary_retry_is_scoped_and_does_not_change_raw(db_session,env):
    data,user=env;pk=data['shop_a_pk']
    r=cold.put(db_session,pk,'C1','human_segment','one',{'entries':[{'id':'a','content':'不是十斤，是五斤'}]},summarize=True)
    db_session.flush();ident=r.id;raw=r.payload_json
    assert not ops.retry(db_session,user,pk,ident).success
    r.state='failed';r.attempts=3;db_session.commit()
    assert not ops.retry(db_session,user,data['shop_b_pk'],ident).success
    assert ops.retry(db_session,user,pk,ident).success
    assert db_session.get(ContextArchive,ident).payload_json==raw


def test_operations_api_permission_boundaries(client,db_session,env,test_users):
    data,user=env
    guest=test_users['guest'];headers=_auth_header(_login_token(client,guest['username'],guest['password']))
    for path in ('operations','experiences','experience-versions','summaries','human-history','answer-history'):
        result=client.get('/api/v1/chat/workspace/'+path,params={'shop_pk':data['shop_a_pk'],'customer_uid':'C1','ident':1},headers=headers).json()
        assert not result['success']
    result=client.post('/api/v1/chat/workspace/experience-review',json={'shop_pk':data['shop_a_pk'],'ident':1,'expected_version':1,'action':'publish','title':'不允许'},headers=headers).json()
    assert not result['success']
