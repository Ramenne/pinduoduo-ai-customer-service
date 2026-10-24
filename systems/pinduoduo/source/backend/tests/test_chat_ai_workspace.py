from types import SimpleNamespace
from app.services import chat_ai_service as ai
from common.models.config_models import LlmConfig
from common.services.service_client import ServiceResponse
from .test_chat_workspace import env
from .test_chat_api import chat_env


def test_mode_keeps_provider_and_key_and_rejects_foreign(db_session,env):
    data,user=env;pk=data['shop_a_pk']
    config=LlmConfig(shop_pk=pk,ai_enabled=False,model_name='fixture-model',api_key_enc='fixture-encrypted',api_base='https://fixture.invalid',instructions='fixture')
    db_session.add(config);db_session.flush()
    assert ai.set_mode(db_session,user,pk,True).success
    assert config.ai_enabled and config.api_key_enc=='fixture-encrypted' and config.api_base=='https://fixture.invalid'
    assert ai.set_mode(db_session,user,pk,False).success and not config.ai_enabled
    assert not ai.set_mode(db_session,user,data['shop_b_pk'],True).success


def test_draft_only_proxies_latest_customer_source(db_session,env,monkeypatch):
    data,user=env;calls=[]
    def post(*args,**kwargs):
        calls.append(args[2]);return ServiceResponse(ok=True,body={'success':True,'data':{'content':'建议','source_message_id':1}})
    monkeypatch.setattr(ai.service_client,'post_json',post)
    assert ai.draft(db_session,user,data['shop_a_pk'],'C1').success
    assert calls[0]['customer_uid']=='C1' and calls[0]['source_message_id']
    assert not ai.draft(db_session,user,data['shop_a_pk'],'missing').success
    assert not ai.draft(db_session,user,data['shop_b_pk'],'C1').success
    assert len(calls)==1


def test_supplement_is_scoped_persistent_and_rejects_old_page(db_session,env):
    from common.services import conversation_control
    data,user=env;pk=data['shop_a_pk']
    state=conversation_control.read(db_session,pk,'C1')
    saved=ai.ai_workspace(db_session,user,pk,'C1',text='本次人工核实的材质',expected_revision=state['revision'])
    assert saved.success and saved.data['control']['mode']=='human'
    db_session.commit()
    read=ai.ai_workspace(db_session,user,pk,'C1')
    assert read.success and read.data['supplement']=='本次人工核实的材质'
    assert not ai.ai_workspace(db_session,user,pk,'C1',text='旧页面覆盖',expected_revision=state['revision']).success
    assert not ai.ai_workspace(db_session,user,data['shop_b_pk'],'C1',text='外店信息',expected_revision=0).success
    assert not ai.ai_workspace(db_session,user,pk,'missing',text='不存在会话',expected_revision=0).success
