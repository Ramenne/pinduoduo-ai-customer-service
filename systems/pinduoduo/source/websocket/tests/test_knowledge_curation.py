import asyncio
import json
import time
from types import SimpleNamespace
import pytest
from sqlalchemy import select
from agent import knowledge_curation as worker
from common.models.knowledge_curation_models import KnowledgeCuration as Task
from common.models.shop_models import Shop
from common.services.contribution_policy import classify, redact


@pytest.mark.parametrize('text', ['这次给您包邮','已退款，本店售后处理中','请问您的尺寸是多少？','材质是什么？','您好，别担心'])
def test_exceptions_operations_questions_greetings_are_not_knowledge(text):
    assert classify(text) is None


def add_task(factory):
    with factory.begin() as s:
        row=Task(shop_pk=1,owner_user_id=9,customer_uid='c',batch_key='fixture-batch',state='pending',
            snapshot_json=json.dumps({'contributions':[{'id':'fact1','content':'材质是纯棉'}],'delivery_ids':[]}))
        s.add(row);s.flush();return row.id


def test_model_output_requires_real_evidence_and_strips_private():
    sources=[{'id':'fact1','content':'材质是纯棉','type':'faq'}]
    assert worker.parse_candidates('[{"title":"材质","content":"材质是纯棉","source_id":"fact1"}]',sources)[0]['status']=='pending_review'
    with pytest.raises(ValueError):
        worker.parse_candidates('[{"title":"材质","content":"材质是纯棉","source_id":"invented"}]',sources)
    assert not worker.parse_candidates('[{"title":"材质","content":"这次材质特殊处理","source_id":"fact1"}]',sources)
    assert not worker.parse_candidates('[{"title":"电话13800138000","content":"材质是纯棉","source_id":"fact1"}]',sources)
    assert '13800138000' not in redact('联系13800138000，地址：某路某号')


def test_task_recovers_after_crash_and_old_lease_cannot_write(delivery_db):
    task_id=add_task(delivery_db)
    first=worker.claim();assert first[0]==task_id and worker.claim() is None
    with delivery_db.begin() as s:s.get(Task,task_id).due_at=time.time()-1
    second=worker.claim();assert second[1]!=first[1]
    assert not worker.settle(*first,[])
    assert worker.settle(*second,[])
    with delivery_db() as s:
        row=s.get(Task,task_id);assert row.state=='skipped' and row.attempts==2


def test_failed_model_remains_pending_then_succeeds_without_publication(monkeypatch,delivery_db):
    task_id=add_task(delivery_db)
    monkeypatch.setattr(worker,'prepare',lambda *a:(SimpleNamespace(),[{'id':'fact1','type':'faq','content':'材质是纯棉'}]))
    async def fail(*a):raise RuntimeError('fixture')
    monkeypatch.setattr(worker,'extract',fail)
    assert asyncio.run(worker.run_once())
    with delivery_db.begin() as s:
        row=s.get(Task,task_id);assert row.state=='pending' and row.error=='RuntimeError';row.due_at=0
    async def succeed(*a):return [{'title':'材质','content':'材质是纯棉','status':'pending_review'}]
    monkeypatch.setattr(worker,'extract',succeed)
    assert asyncio.run(worker.run_once())
    with delivery_db() as s:
        row=s.get(Task,task_id);assert row.state=='ready' and json.loads(row.result_json)[0]['status']=='pending_review'
        from common.models.knowledge_models import CustomerServiceKnowledge,ProductKnowledge
        assert s.scalar(select(CustomerServiceKnowledge)) is None and s.scalar(select(ProductKnowledge)) is None


def test_owner_change_fences_result_and_cancels_old_task(delivery_db):
    task_id=add_task(delivery_db);claimed=worker.claim()
    with delivery_db.begin() as s:s.get(Shop,1).owner_user_id=99
    worker.settle(*claimed,[{'title':'应被挡住'}])
    with delivery_db() as s:
        row=s.get(Task,task_id);assert row.state=='cancelled' and row.result_json is None


def test_manual_contribution_acceptance_does_not_forge_delivery_confirmation(monkeypatch, delivery_db):
    from common.models.message_delivery_models import MessageDelivery
    from common.models.config_models import LlmConfig
    task_id=add_task(delivery_db)
    with delivery_db.begin() as s:
        d=MessageDelivery(shop_pk=1,customer_uid='c',operation_key='manual:fixture',kind='text',
            body_hash='a'*64,body=json.dumps('保质期为12个月'),state='accepted',request_id='fixture',due_at=0)
        s.add(d);s.flush();delivery_id=d.id
        s.get(Task,task_id).snapshot_json=json.dumps({'contributions':[],'delivery_ids':[delivery_id]})
        s.add(LlmConfig(shop_pk=1,model_name='fixture'))
    claimed=worker.claim()
    monkeypatch.setattr(worker.AgentConfig,'from_llm_config',lambda _:SimpleNamespace(api_key='fixture'))
    _,sources=worker.prepare(*claimed)
    assert sources==[{'id':f'delivery:{delivery_id}','content':'保质期为12个月','type':'faq','delivery_state':'accepted'}]
    with delivery_db.begin() as s:
        assert s.get(MessageDelivery,delivery_id).state=='accepted'
        s.get(MessageDelivery,delivery_id).state='uncertain'
    with pytest.raises(ValueError,match='awaiting_delivery_confirmation'):
        worker.prepare(*claimed)
