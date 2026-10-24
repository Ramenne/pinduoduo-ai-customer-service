import json
import pytest
from sqlalchemy import select, func
from app.services import knowledge_curation_service as service
from common.services import knowledge_publication as core
from common.models.knowledge_models import Product, ProductKnowledge, CustomerServiceKnowledge
from common.models.knowledge_curation_models import KnowledgeCuration
from common.models.shop_models import Shop
from common.models.user_models import SysPermission, SysRolePermission
from .test_chat_workspace import env
from .test_chat_api import chat_env, _login_token, _auth_header


@pytest.fixture
def setup(db_session, env):
    data,user=env
    for resource in ('product_knowledge','cs_knowledge'):
        for action in ('view','create','update'):
            permission=SysPermission(resource_key=resource,action=action)
            db_session.add(permission);db_session.flush()
            db_session.add(SysRolePermission(role_id=user.role_id,permission_id=permission.id))
    product=Product(shop_pk=data['shop_a_pk'],goods_id='GOODS-A',goods_name='本地商品A',status=1)
    db_session.add(product);db_session.flush()
    return data,user


def task(session,pk,owner,category='product',content='材质为纯棉',title='商品材质',kind='faq'):
    import uuid
    row=KnowledgeCuration(shop_pk=pk,owner_user_id=owner,customer_uid='C1',batch_key=str(uuid.uuid4()),state='ready',snapshot_json='{}',
        result_json=json.dumps([{'title':title,'content':content,'category':category,'knowledge_type':kind,'status':'pending_review','source_id':'fact'}]))
    session.add(row);session.flush();return row


def command(session,user,row,category='product',goods_id='GOODS-A'):
    c=json.loads(row.result_json)[0]
    result=service.preview(session,user,row.id,0,category,c['title'],goods_id)
    assert result.success,result.message
    return {'decision':'publish','category':category,'title':c['title'],'content':c['content'],'goods_id':goods_id,
        'candidate_token':result.data['candidate']['candidate_token'],'target_hash':result.data['target_hash'],
        'mode':'append' if result.data['target'] else 'create'}


def test_publish_product_preserves_existing_fields_and_replay_is_once(db_session,setup):
    data,user=setup;pk=data['shop_a_pk']
    existing=ProductKnowledge(shop_pk=pk,goods_id='GOODS-A',goods_name='商品A',extracted_content='保质期一年',price=12,specifications='规格M',status=1)
    db_session.add(existing);db_session.flush()
    row=task(db_session,pk,user.id);payload=command(db_session,user,row)
    first=service.review(db_session,user,row.id,0,payload)
    assert first.success and first.data['action']=='merged'
    db_session.commit()
    second=service.review(db_session,user,row.id,0,payload)
    assert second.success and second.data==first.data
    assert db_session.scalar(select(func.count()).select_from(ProductKnowledge))==1
    db_session.refresh(existing)
    assert existing.extracted_content=='保质期一年\n\n材质为纯棉'
    assert float(existing.price)==12 and existing.specifications=='规格M'
    assert first.data['before']['content']=='保质期一年'
    from agent.round_knowledge import load_round_knowledge
    knowledge=load_round_knowledge(pk)
    assert '纯棉' in knowledge.execute('get_product_knowledge',{'goods_id':'GOODS-A'})
    assert knowledge.evidence()['references']


def test_policy_uses_existing_service_store_and_search(db_session,setup):
    data,user=setup;pk=data['shop_a_pk']
    row=task(db_session,pk,user.id,'service','本店未拆封商品可在七天内申请退货','退货规则','policy')
    payload=command(db_session,user,row,'service','')
    result=service.review(db_session,user,row.id,0,payload)
    assert result.success and result.data['current_matches']
    db_session.commit()
    knowledge=db_session.get(CustomerServiceKnowledge,result.data['target_id'])
    assert knowledge.tags=='商户政策' and knowledge.enabled
    from common.services.kb_service import search
    assert knowledge in search(db_session,pk,query='退货').customer_service_knowledge
    from agent.round_knowledge import load_round_knowledge
    assert '七天内' in load_round_knowledge(pk).execute('search_customer_service_knowledge',{'query':'退货'})


def test_same_title_content_reuses_and_does_not_duplicate(db_session,setup):
    data,user=setup;pk=data['shop_a_pk']
    existing=CustomerServiceKnowledge(shop_pk=pk,title='保存方法',content='常温保存',enabled=True)
    db_session.add(existing);db_session.flush()
    row=task(db_session,pk,user.id,'service','常温保存','保存方法')
    payload=command(db_session,user,row,'service','')
    result=service.review(db_session,user,row.id,0,payload)
    assert result.success and result.data['action']=='reused' and result.data['target_id']==existing.id
    assert db_session.scalar(select(func.count()).select_from(CustomerServiceKnowledge))==1


def test_stale_preview_cannot_overwrite_or_duplicate(db_session,setup):
    data,user=setup;pk=data['shop_a_pk'];row=task(db_session,pk,user.id)
    payload=command(db_session,user,row)
    existing=ProductKnowledge(shop_pk=pk,goods_id='GOODS-A',extracted_content='另一个窗口刚保存的知识',status=1)
    db_session.add(existing);db_session.flush()
    assert not service.review(db_session,user,row.id,0,payload).success
    assert existing.extracted_content=='另一个窗口刚保存的知识'
    assert json.loads(row.result_json)[0]['status']=='pending_review'
    payload=command(db_session,user,row);payload['mode']='replace'
    result=service.review(db_session,user,row.id,0,payload)
    assert result.success and result.data['action']=='revised'
    assert result.data['before']['content']=='另一个窗口刚保存的知识'


def test_disabled_scope_private_missing_product_and_reject(db_session,setup):
    data,user=setup;pk=data['shop_a_pk'];row=task(db_session,pk,user.id)
    payload=command(db_session,user,row);payload['goods_id']='missing'
    assert not service.review(db_session,user,row.id,0,payload).success
    assert db_session.scalar(select(ProductKnowledge)) is None
    payload=command(db_session,user,row);payload['content']='材质纯棉，电话13800138000'
    assert not service.review(db_session,user,row.id,0,payload).success
    payload=command(db_session,user,row);payload['decision']='reject'
    assert service.review(db_session,user,row.id,0,payload).success
    assert service.review(db_session,user,row.id,0,payload).success
    assert db_session.scalar(select(ProductKnowledge)) is None
    row2=task(db_session,pk,user.id)
    existing=ProductKnowledge(shop_pk=pk,goods_id='GOODS-A',extracted_content='停用知识',status=0)
    db_session.add(existing);db_session.flush()
    payload=command(db_session,user,row2)
    assert not service.review(db_session,user,row2.id,0,payload).success
    db_session.get(Shop,pk).owner_user_id=data['other_id'] if 'other_id' in data else user.id+100
    db_session.flush()
    assert not service.review(db_session,user,row2.id,0,payload).success


def test_api_enforces_knowledge_permission_not_just_chat(client,db_session,env,test_users):
    data,user=env;row=task(db_session,data['shop_a_pk'],user.id,'service','本店统一发货','发货规则','policy')
    person=test_users['manager'];h=_auth_header(_login_token(client,person['username'],person['password']))
    path=f'/api/v1/knowledge-curation/tasks/{row.id}/candidates/0/preview'
    result=client.get(path,params={'category':'service','title':'发货规则'},headers=h).json()
    assert not result['success'] and result['code']==40300



def test_disabled_duplicate_does_not_block_unique_enabled_subject(db_session,setup):
    data,user=setup;pk=data['shop_a_pk']
    db_session.add(ProductKnowledge(shop_pk=pk,goods_id='GOODS-A',extracted_content='停用旧版',status=0))
    active=ProductKnowledge(shop_pk=pk,goods_id='GOODS-A',extracted_content='保质期一年',status=1)
    db_session.add(active);db_session.flush()
    row=task(db_session,pk,user.id);payload=command(db_session,user,row)
    result=service.review(db_session,user,row.id,0,payload)
    assert result.success and result.data['target_id']==active.id
    assert '停用旧版' not in result.data['after']['content']


def test_correction_follows_original_id_even_after_title_rename(db_session,setup):
    from common.services import knowledge_publication as publication
    data,user=setup;pk=data['shop_a_pk']
    existing=CustomerServiceKnowledge(shop_pk=pk,title='最新退货政策',content='原政策',enabled=True)
    db_session.add(existing);db_session.flush()
    row=task(db_session,pk,user.id,'service','修订政策','旧标题','policy')
    candidates=json.loads(row.result_json)
    candidates[0]['corrected_reference']={'source':'pdd_customer_service_knowledge','id':existing.id,'version_no':1}
    row.result_json=json.dumps(candidates)
    preview=publication.preview(db_session,row.id,0,user.id,category='service',title='旧标题')
    assert preview['target']['id']==existing.id and preview['target']['title']=='最新退货政策'
    with pytest.raises(ValueError):
        publication.preview(db_session,row.id,0,user.id,category='product',title='任意',goods_id='GOODS-A')
