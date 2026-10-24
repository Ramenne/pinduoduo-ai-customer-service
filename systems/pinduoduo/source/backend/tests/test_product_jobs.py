"""提交/去重→执行→保存→事件快照/恢复与权限。"""
from datetime import timedelta
from sqlalchemy import select
from app.services import product_jobs, product_sync_client
from common.models.knowledge_models import Product
from common.models.product_sync_models import ProductSyncJob
from common.models.shop_models import Shop
from common.utils.time_utils import now_beijing_naive
from .test_products_api import product_env, _login_token, _auth_header


def auth(client, users):
    p = users['manager']
    return _auth_header(_login_token(client, p['username'], p['password']))


def specs(*args, **kwargs):
    return product_sync_client.ProductDetailResult(ok=True, detail={'specifications': ['蓝色']})


def test_enqueue_deduplicates_without_platform_request(client, test_users, product_env, monkeypatch, db_session):
    def forbidden(*a, **k):
        raise AssertionError('request must not fetch platform')
    monkeypatch.setattr(product_sync_client, 'pull_products', forbidden)
    h = auth(client, test_users)
    payload = {'shop_pk': product_env['shop_a_pk']}
    a = client.post('/api/v1/products/sync-jobs', json=payload, headers=h).json()
    b = client.post('/api/v1/products/sync-jobs', json=payload, headers=h).json()
    assert a['success'] and b['success'] and a['data']['state'] == 'queued'
    assert a['data']['revision'] == b['data']['revision']
    assert len(list(db_session.scalars(select(ProductSyncJob)))) == 1


def test_catalog_commits_before_specs_and_events_read_persisted_state(client, test_users, product_env, monkeypatch, db_session):
    h = auth(client, test_users)
    monkeypatch.setattr(product_sync_client, 'pull_products', lambda *a, **k:
        product_sync_client.ProductPullResult(ok=True, products=[{'goods_id': 'G2', 'goods_name': '新商品'}]))
    def detail(*args):
        from common.db.session import session_scope
        with session_scope() as s:
            assert s.scalar(select(Product).where(Product.goods_id == 'G2')) is not None
        return specs()
    monkeypatch.setattr(product_sync_client, 'fetch_product_detail', detail)
    client.post('/api/v1/products/sync-jobs', json={'shop_pk': product_env['shop_a_pk']}, headers=h)
    product_jobs.run(product_jobs.claim_next())
    data = client.get('/api/v1/products/sync-status', params={'shop_pk': product_env['shop_a_pk']}, headers=h).json()['data']
    assert data['state'] == 'succeeded' and data['completed'] == 1 and data['finished_at']
    db_session.expire_all()
    item = db_session.scalar(select(Product).where(Product.goods_id == 'G2'))
    assert item.synced_at and item.specs_updated_at and item.specs_checked_at
    monkeypatch.setattr(product_sync_client, 'fetch_product_detail', lambda *a, **k: 1/0)
    assert client.get(f'/api/v1/products/{item.id}/detail', headers=h).json()['data']['specifications'] == ['蓝色']
    stream = client.get('/api/v1/products/sync-events', params={'shop_pk': product_env['shop_a_pk']}, headers=h)
    assert 'text/event-stream' in stream.headers['content-type'] and 'succeeded' in stream.text


def test_failure_keeps_specs_and_retry_recovers(client, test_users, product_env, monkeypatch, db_session):
    h = auth(client, test_users)
    item = db_session.get(Product, product_env['product_id'])
    item.specifications = '["原规格"]'; db_session.commit()
    monkeypatch.setattr(product_sync_client, 'fetch_product_detail', lambda *a, **k:
        product_sync_client.ProductDetailResult(ok=False, message='服务暂不可用'))
    path = f'/api/v1/products/{item.id}/refresh'
    assert client.post(path, headers=h).json()['success']
    product_jobs.run(product_jobs.claim_next())
    d = client.get(f'/api/v1/products/{item.id}/detail', headers=h).json()['data']
    assert d['specifications'] == ['原规格'] and d['specs_error'] == '服务暂不可用'
    assert d['specs_updated_at'] is None and d['specs_checked_at']
    monkeypatch.setattr(product_sync_client, 'fetch_product_detail', specs)
    assert client.post(path, headers=h).json()['success']
    product_jobs.run(product_jobs.claim_next())
    d = client.get(f'/api/v1/products/{item.id}/detail', headers=h).json()['data']
    assert d['specifications'] == ['蓝色'] and d['specs_updated_at'] and d['specs_error'] is None


def test_expired_worker_cannot_write_after_recovery(client, test_users, product_env, monkeypatch, db_session):
    h = auth(client, test_users)
    client.post(f'/api/v1/products/{product_env["product_id"]}/refresh', headers=h)
    first = product_jobs.claim_next()
    job = db_session.get(ProductSyncJob, first[0]); db_session.refresh(job)
    job.updated_at = now_beijing_naive() - timedelta(seconds=130); db_session.commit()
    second = product_jobs.claim_next()
    assert second[0] == first[0] and second[1] != first[1]
    monkeypatch.setattr(product_sync_client, 'fetch_product_detail', specs)
    product_jobs.run(first)
    db_session.expire_all()
    assert db_session.get(Product, product_env['product_id']).specifications is None
    assert db_session.get(ProductSyncJob, first[0]).state == 'running'
    product_jobs.run(second)
    db_session.expire_all()
    assert db_session.get(ProductSyncJob, first[0]).state == 'succeeded'


def test_owner_and_login_isolation(client, test_users, product_env):
    h = auth(client, test_users)
    for path in ('sync-status', 'sync-events'):
        assert not client.get(f'/api/v1/products/{path}', params={'shop_pk': product_env['shop_b_pk']}, headers=h).json()['success']
        assert not client.get(f'/api/v1/products/{path}', params={'shop_pk': product_env['shop_a_pk']}).json()['success']
    assert not client.post('/api/v1/products/sync-jobs', json={'shop_pk': product_env['shop_b_pk']}, headers=h).json()['success']


def test_signature_failure_stops_shop(client, test_users, product_env, monkeypatch, db_session):
    shop = db_session.get(Shop, product_env['shop_a_pk'])
    db_session.add(Product(shop_pk=shop.id, goods_id='G2', status=1)); db_session.commit()
    product_jobs.enqueue(db_session, shop, kind='specs'); db_session.commit()
    calls = []
    def missing(*args):
        calls.append(args)
        return product_sync_client.ProductDetailResult(ok=False, signature_missing=True, message='重新授权')
    monkeypatch.setattr(product_sync_client, 'fetch_product_detail', missing)
    product_jobs.run(product_jobs.claim_next())
    db_session.expire_all()
    job = db_session.scalar(select(ProductSyncJob))
    assert len(calls) == 1 and job.state == 'failed' and job.message == '重新授权'


def test_changed_owner_or_revoked_actor_cannot_receive_late_result(client, test_users, product_env, monkeypatch, db_session):
    from common.db.session import session_scope
    from common.models.user_models import SysUser
    h = auth(client, test_users)
    client.post(f'/api/v1/products/{product_env["product_id"]}/refresh', headers=h)
    def delayed(*args):
        with session_scope() as s:
            s.get(SysUser, product_env['manager_id']).status = 0
        return specs()
    monkeypatch.setattr(product_sync_client, 'fetch_product_detail', delayed)
    product_jobs.run(product_jobs.claim_next())
    db_session.expire_all()
    assert db_session.get(Product, product_env['product_id']).specifications is None
    assert db_session.scalar(select(ProductSyncJob)).state == 'failed'


def test_different_pending_scope_is_not_silently_dropped(client, test_users, product_env, db_session):
    h = auth(client, test_users)
    client.post(f'/api/v1/products/{product_env["product_id"]}/refresh', headers=h)
    response = client.post('/api/v1/products/sync-jobs', json={'shop_pk': product_env['shop_a_pk']}, headers=h).json()
    assert not response['success'] and response['code'] == 40900
