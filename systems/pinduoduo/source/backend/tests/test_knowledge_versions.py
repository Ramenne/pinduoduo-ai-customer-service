import json
import pytest
from sqlalchemy import select,func
from app.services import product_knowledge_service as product,cs_knowledge_service as service,knowledge_version_service as api
from common.services import knowledge_versions as core
from common.models.knowledge_models import ProductKnowledge,CustomerServiceKnowledge
from common.models.knowledge_version_models import KnowledgeVersion
from .test_knowledge_publication import setup,task,command
from .test_chat_workspace import env
from .test_chat_api import chat_env


def rows(s,kind,item):
    return list(s.scalars(select(KnowledgeVersion).where(KnowledgeVersion.kind==kind,KnowledgeVersion.item_id==item).order_by(KnowledgeVersion.version_no)))


def test_edit_disable_restore_and_repeated_restore_make_one_current_chain(db_session,setup):
    data,user=setup;pk=data['shop_a_pk']
    made=service.create_cs_knowledge(db_session,user,pk,'发货时效','48小时发货');item=made.data['id']
    assert len(rows(db_session,'service',item))==1
    service.update_cs_knowledge(db_session,user,item,content='24小时发货')
    service.set_cs_knowledge_status(db_session,user,item,False)
    history=api.history(db_session,user,'service',item).data
    assert [r['version_no'] for r in history['list']]==[3,2,1]
    v1=rows(db_session,'service',item)[0]
    restored=api.restore(db_session,user,'service',item,v1.id,history['current']['hash'])
    assert restored.success and restored.data['version']['version_no']==4
    assert db_session.get(CustomerServiceKnowledge,item).content=='48小时发货'
    assert db_session.get(CustomerServiceKnowledge,item).enabled
    assert api.restore(db_session,user,'service',item,v1.id,history['current']['hash']).success
    assert len(rows(db_session,'service',item))==4
    from common.services.kb_service import search
    assert search(db_session,pk,query='发货').customer_service_knowledge[0].content=='48小时发货'


def test_legacy_baseline_keeps_unknown_actor_and_numeric_values_stable(db_session,setup):
    data,user=setup;pk=data['shop_a_pk'];item=ProductKnowledge(shop_pk=pk,goods_id='GOODS-A',price=12,extracted_content='材质纯棉',status=1)
    db_session.add(item);db_session.flush();db_session.refresh(item)
    assert api.history(db_session,user,'product',item.id).data['current']['version_no'] is None
    product.update_product_knowledge(db_session,user,item.id,extracted_content='材质羊毛')
    revisions=rows(db_session,'product',item.id)
    assert [r.operation for r in revisions]==['baseline','edit'] and revisions[0].created_by is None
    assert revisions[1].created_by==user.id
    product.update_product_knowledge(db_session,user,item.id,price=12.0)
    db_session.commit()
    assert len(rows(db_session,'product',item.id))==2
    product.delete_product_knowledge(db_session,user,item.id)
    assert rows(db_session,'product',item.id)[-1].operation=='delete'


def test_import_dedupe_and_publication_share_history(db_session,setup):
    from app.services import knowledge_curation_service as publication
    data,user=setup;pk=data['shop_a_pk']
    imported=service.import_cs_knowledge(db_session,user,pk,[{'title':'保存','content':'常温保存'},{'title':'保存','content':'常温保存'}])
    assert imported.data['imported']==1 and db_session.scalar(select(func.count()).select_from(KnowledgeVersion))==1
    service.import_cs_knowledge(db_session,user,pk,[{'title':'保存','content':'常温保存'}])
    assert db_session.scalar(select(func.count()).select_from(KnowledgeVersion))==1
    t=task(db_session,pk,user.id,'service','干燥保存','保存')
    result=publication.review(db_session,user,t.id,0,command(db_session,user,t,'service',''))
    assert result.success and result.data['knowledge_version_no']==2
    current=rows(db_session,'service',result.data['target_id'])
    assert current[-1].operation=='curation_publish' and current[-1].id==result.data['knowledge_version_id']


def test_stale_restore_and_foreign_revision_do_not_change_anything(db_session,setup):
    data,user=setup;pk=data['shop_a_pk']
    item=service.create_cs_knowledge(db_session,user,pk,'材质','纯棉').data['id'];old=rows(db_session,'service',item)[0]
    view=api.history(db_session,user,'service',item).data
    service.update_cs_knowledge(db_session,user,item,content='羊毛')
    assert not api.restore(db_session,user,'service',item,old.id,view['current']['hash']).success
    assert db_session.get(CustomerServiceKnowledge,item).content=='羊毛' and len(rows(db_session,'service',item))==2
    other=service.create_cs_knowledge(db_session,user,pk,'规格','M码').data['id']
    assert not api.detail(db_session,user,'service',other,old.id).success
    assert not api.restore(db_session,user,'service',other,old.id,view['current']['hash']).success


def test_version_failure_rolls_back_the_knowledge_write(db_session,setup,monkeypatch):
    data,user=setup;item=service.create_cs_knowledge(db_session,user,data['shop_a_pk'],'材质','纯棉').data['id']
    real=core.record
    def fail(session,kind,row,actor,operation,*args):
        if operation=='edit':raise RuntimeError('history storage failure')
        return real(session,kind,row,actor,operation,*args)
    monkeypatch.setattr(core,'record',fail)
    with pytest.raises(RuntimeError):service.update_cs_knowledge(db_session,user,item,content='错误覆盖')
    assert db_session.get(CustomerServiceKnowledge,item).content=='纯棉'
    assert len(rows(db_session,'service',item))==1


def test_restore_disabled_version_requires_disable_permission(db_session,setup):
    data,user=setup;item=service.create_cs_knowledge(db_session,user,data['shop_a_pk'],'材质','纯棉',enabled=False).data['id']
    disabled=rows(db_session,'service',item)[0]
    service.set_cs_knowledge_status(db_session,user,item,True)
    h=api.history(db_session,user,'service',item).data
    result=api.restore(db_session,user,'service',item,disabled.id,h['current']['hash'])
    assert not result.success and result.code==40300


def test_round_evidence_points_to_matching_current_knowledge_version(db_session,setup):
    from agent.round_knowledge import load_round_knowledge
    data,user=setup;pk=data['shop_a_pk']
    item=product.upsert_product_knowledge(db_session,user,pk,'GOODS-A',extracted_content='材质纯棉',price=12).data['id']
    db_session.commit();knowledge=load_round_knowledge(pk);knowledge.execute('get_product_knowledge',{'goods_id':'GOODS-A'})
    reference=next(r for r in knowledge.evidence()['references'] if r['source']=='pdd_product_knowledge')
    assert reference['version_no']==1 and reference['knowledge_version_id']==rows(db_session,'product',item)[0].id



def test_service_round_evidence_has_matching_version(db_session,setup):
    from agent.round_knowledge import load_round_knowledge
    data,user=setup;pk=data['shop_a_pk']
    item=service.create_cs_knowledge(db_session,user,pk,'发货政策','本店48小时内发货').data['id']
    db_session.commit();knowledge=load_round_knowledge(pk)
    knowledge.execute('search_customer_service_knowledge',{'query':'发货政策'})
    reference=next(r for r in knowledge.evidence()['references'] if r['source']=='pdd_customer_service_knowledge')
    assert reference['version_no']==1 and reference['knowledge_version_id']==rows(db_session,'service',item)[0].id


def test_foreign_shop_history_and_restore_are_denied(db_session,setup):
    data,user=setup
    foreign=CustomerServiceKnowledge(shop_pk=data['shop_b_pk'],title='另一店知识',content='不可跨店读取',enabled=True)
    db_session.add(foreign);db_session.flush()
    revision=core.record(db_session,'service',foreign,None,'create')
    assert not api.history(db_session,user,'service',foreign.id).success
    assert not api.detail(db_session,user,'service',foreign.id,revision.id).success
    assert not api.restore(db_session,user,'service',foreign.id,revision.id,revision.content_hash).success
