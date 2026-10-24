from sqlalchemy import select
from app.services import product_service, product_jobs, product_sync_client
from common.models.knowledge_models import Product
from common.models.product_sync_models import ProductSyncJob
from common.models.shop_models import Shop
from .test_products_api import product_env


def test_complete_snapshot_marks_absent_and_preserves_manual_disabled(client,test_users,product_env,db_session):
    pk=product_env['shop_a_pk']
    old=db_session.get(Product,product_env['product_id'])
    other=Product(shop_pk=product_env['shop_b_pk'],goods_id='other',status=1)
    disabled=Product(shop_pk=pk,goods_id='disabled',status=0)
    db_session.add_all([other,disabled]);db_session.flush()
    product_service._upsert_pulled_products(db_session,pk,[{'goods_id':'disabled','goods_name':'still disabled'}])
    assert old.platform_on_sale==0 and old.status==1 and old.synced_at
    assert disabled.platform_on_sale==1 and disabled.status==0
    assert other.platform_on_sale is None
    product_service._upsert_pulled_products(db_session,pk,[])
    assert disabled.platform_on_sale==0 and disabled.status==0


def test_empty_catalog_does_not_request_stale_product_details(client,test_users,product_env,db_session,monkeypatch):
    shop=db_session.get(Shop,product_env['shop_a_pk'])
    product_jobs.enqueue(db_session,shop);db_session.commit()
    monkeypatch.setattr(product_sync_client,'pull_products',lambda *a:product_sync_client.ProductPullResult(ok=True,products=[]))
    monkeypatch.setattr(product_sync_client,'fetch_product_detail',lambda *a:(_ for _ in ()).throw(AssertionError('off sale must not be fetched')))
    product_jobs.run(product_jobs.claim_next());db_session.expire_all()
    job=db_session.scalar(select(ProductSyncJob))
    item=db_session.get(Product,product_env['product_id'])
    assert job.state=='succeeded' and job.total==0 and item.platform_on_sale==0


def test_partial_or_failed_pull_preserves_catalog(client,test_users,product_env,db_session,monkeypatch):
    shop=db_session.get(Shop,product_env['shop_a_pk'])
    product_jobs.enqueue(db_session,shop);db_session.commit()
    monkeypatch.setattr(product_sync_client,'pull_products',lambda *a:product_sync_client.ProductPullResult(ok=False,message='分页未完成'))
    product_jobs.run(product_jobs.claim_next());db_session.expire_all()
    assert db_session.get(Product,product_env['product_id']).platform_on_sale is None
    assert db_session.scalar(select(ProductSyncJob)).state=='failed'
