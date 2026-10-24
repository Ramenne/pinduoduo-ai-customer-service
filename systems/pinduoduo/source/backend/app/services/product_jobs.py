"""商品同步队列：先提交任务，再唤醒；按店铺去重，重启后恢复，旧任务不能回写。"""
from datetime import timedelta
import logging
import threading
from uuid import uuid4

from sqlalchemy import or_, select, update, func
from sqlalchemy.exc import IntegrityError

from app.core.errors import BusinessError
from app.core import permission
from app.core.data_scope import build_data_scope, is_in_scope
from app.services import product_spec_codec, product_sync_client
from common.db.session import session_scope
from common.models.knowledge_models import Product
from common.models.product_sync_models import ProductSyncJob
from common.models.shop_models import Shop
from common.models.user_models import SysUser
from common.utils.time_utils import now_beijing_naive, safe_isoformat

logger = logging.getLogger(__name__)
ACTIVE = ('queued', 'running')
LEASE_SECONDS = 120
_wake = threading.Event()
_stop = threading.Event()
_thread = None
_start_lock = threading.Lock()


def snapshot(job):
    if job is None:
        return {'state': 'idle', 'revision': 0, 'message': '尚未同步'}
    return {key: getattr(job, key) for key in (
        'id', 'shop_pk', 'product_id', 'kind', 'state', 'revision',
        'total', 'completed', 'failed', 'message')} | {
        'updated_at': safe_isoformat(job.updated_at),
        'finished_at': safe_isoformat(job.finished_at)}


def enqueue(session, shop, *, product_id=None, kind='catalog', actor_id=None):
    """请求会话先持久保存，提交成功后调用 wake；不在未提交事务中启动线程。"""
    job = session.scalar(select(ProductSyncJob).where(
        ProductSyncJob.shop_pk == shop.id).with_for_update())
    if job and job.state in ACTIVE and job.owner_user_id == shop.owner_user_id:
        if not (job.kind == 'catalog' or
                (kind == 'specs' and job.product_id in (None, product_id))):
            raise BusinessError(40900, '店铺已有其他同步任务，请完成后再试')
        return snapshot(job)
    if (job and job.state == 'succeeded' and kind == job.kind and product_id == job.product_id
            and job.finished_at and (now_beijing_naive() - job.finished_at).total_seconds() < 60):
        raise BusinessError(40900, '刚刚已同步，请稍后再试；当前结果已保存')
    values = dict(product_id=product_id, kind=kind, state='queued',
                  created_by=actor_id,
                  owner_user_id=shop.owner_user_id, run_token=uuid4().hex,
                  total=0, completed=0, failed=0, message='等待后台同步', finished_at=None)
    if job is None:
        try:
            with session.begin_nested():
                job = ProductSyncJob(shop_pk=shop.id, **values)
                session.add(job)
                session.flush()
        except IntegrityError:
            # 首次并发创建由店铺唯一约束收敛；读取胜出请求的同一任务。
            job = session.scalar(select(ProductSyncJob).where(
                ProductSyncJob.shop_pk == shop.id).with_for_update())
    else:
        for key, value in values.items():
            setattr(job, key, value)
        job.revision += 1
        session.flush()
    return snapshot(job)


def wake():
    _wake.set()


def start():
    global _thread
    with _start_lock:
        if _thread and _thread.is_alive():
            return
        _stop.clear()
        _thread = threading.Thread(target=_loop, name='product-sync-worker', daemon=True)
        _thread.start()
        wake()


def stop():
    _stop.set()
    wake()
    if _thread:
        _thread.join(timeout=2)


def _loop():
    while not _stop.is_set():
        _wake.wait(10)  # 只检查持久任务/失效租约，绝不定时向平台拉取商品。
        _wake.clear()
        try:
            while not _stop.is_set():
                claim = claim_next()
                if claim is None:
                    break
                run(claim)
        except Exception:
            logger.exception('商品同步执行异常；任务租约到期后可恢复')


def claim_next():
    """CAS 认领，适用于多个 backend 进程；超时租约由新 token 隔离迟到结果。"""
    with session_scope() as session:
        stale = now_beijing_naive() - timedelta(seconds=LEASE_SECONDS)
        eligible = or_(ProductSyncJob.state == 'queued',
                       (ProductSyncJob.state == 'running') & (ProductSyncJob.updated_at < stale))
        job = session.scalar(select(ProductSyncJob).where(eligible)
                             .order_by(ProductSyncJob.updated_at).limit(1))
        if job is None:
            return None
        token = uuid4().hex
        changed = session.execute(update(ProductSyncJob).where(
            ProductSyncJob.id == job.id, ProductSyncJob.run_token == job.run_token,
            eligible).values(state='running', run_token=token,
                             revision=ProductSyncJob.revision + 1,
                             message='正在后台同步', completed=0, failed=0,
                             updated_at=now_beijing_naive()))
        if not changed.rowcount:
            return None
        return job.id, token


def _locked(session, claim):
    job = session.scalar(select(ProductSyncJob).where(
        ProductSyncJob.id == claim[0], ProductSyncJob.run_token == claim[1],
        ProductSyncJob.state == 'running').with_for_update())
    if job is None:
        raise RuntimeError('任务已由其他执行器接续')
    shop = session.get(Shop, job.shop_pk)
    if (shop is None or shop.status != 1 or shop.merged_into_shop_pk is not None
            or shop.owner_user_id != job.owner_user_id):
        raise RuntimeError('店铺已停用或归属发生变化')
    if job.created_by is not None:
        actor = session.get(SysUser, job.created_by)
        if (actor is None or actor.status != 1 or
                not permission.check(actor, 'product', 'update', session=session) or
                not is_in_scope(build_data_scope(actor, session=session), shop.owner_user_id)):
            raise RuntimeError('同步发起者的权限已失效')
    return job, shop


def _touch(job, **values):
    for key, value in values.items():
        setattr(job, key, value)
    job.revision += 1
    job.updated_at = now_beijing_naive()


def run(claim):
    try:
        _execute(claim)
    except Exception:
        logger.exception('商品后台同步未完成：job_id=%s', claim[0])
        with session_scope() as session:
            session.execute(update(ProductSyncJob).where(
                ProductSyncJob.id == claim[0], ProductSyncJob.run_token == claim[1],
                ProductSyncJob.state == 'running').values(
                    state='failed', message='同步未完成，已保留原数据，可重试',
                    finished_at=now_beijing_naive(), updated_at=now_beijing_naive(),
                    revision=ProductSyncJob.revision + 1))


def _execute(claim):
    catalog_note = ''
    with session_scope() as session:
        job, shop = _locked(session, claim)
        kind, product_id = job.kind, job.product_id
        shop_pk, shop_id, owner = shop.id, shop.shop_id, shop.owner_user_id

    if kind == 'catalog':
        result = product_sync_client.pull_products(shop_pk, shop_id, owner)
        with session_scope() as session:
            job, _ = _locked(session, claim)
            if not result.ok:
                _touch(job, state='failed', message=result.message or '商品同步失败',
                       finished_at=now_beijing_naive())
                return
            from app.services.product_service import _upsert_pulled_products
            _upsert_pulled_products(session, shop_pk, result.products)
            off_sale = session.scalar(select(func.count()).select_from(Product).where(
                Product.shop_pk == shop_pk, Product.platform_on_sale == 0)) or 0
            catalog_note = f'，平台在售 {len(result.products)} 件、未在售 {off_sale} 件'
            _touch(job, message='商品列表已保存，正在更新规格')
        # 独立事务已提交，规格任务才能读取刚同步的商品。

    with session_scope() as session:
        job, _ = _locked(session, claim)
        query = select(Product.id).where(Product.shop_pk == shop_pk, Product.status == 1,
            or_(Product.platform_on_sale.is_(None), Product.platform_on_sale == 1))
        if product_id is not None:
            query = query.where(Product.id == product_id)
        targets = list(session.scalars(query.order_by(Product.id)))
        if product_id is not None and not targets:
            _touch(job, state='failed', message='商品已停用或不存在，未更新规格',
                   finished_at=now_beijing_naive())
            return
        _touch(job, total=len(targets))

    for product_pk in targets:
        if _stop.is_set():
            # 正常关闭立即放回队列；异常退出由租约恢复。
            with session_scope() as session:
                job, _ = _locked(session, claim)
                _touch(job, state='queued', message='等待服务恢复后继续')
            return
        with session_scope() as session:
            job, _ = _locked(session, claim)
            product = session.get(Product, product_pk)
            if product is None or product.shop_pk != shop_pk or product.status != 1:
                continue
            goods_id = product.goods_id
        result = product_sync_client.fetch_product_detail(shop_id, owner, goods_id)
        with session_scope() as session:
            job, _ = _locked(session, claim)
            product = session.get(Product, product_pk)
            if product is None or product.shop_pk != shop_pk or product.goods_id != goods_id:
                continue
            product.specs_checked_at = now_beijing_naive()
            if result.ok:
                encoded = product_spec_codec.encode_specifications(
                    (result.detail or {}).get('specifications'))
                if encoded is not None:
                    product.specifications = encoded
                    product.specs_updated_at = now_beijing_naive()
                    product.specs_error = None
                else:
                    product.specs_error = '平台本次未返回规格，已保留原数据'
                    job.failed += 1
            else:
                product.specs_error = (result.message or '规格更新失败')[:256]
                job.failed += 1
            _touch(job, completed=job.completed + 1)
            if result.signature_missing:
                _touch(job, state='failed', message=result.message or '登录状态需恢复，请重新授权店铺',
                       finished_at=now_beijing_naive())
                return  # 店铺级授权失败不再逐个打同样失败的请求。
        if _stop.wait(2):
            continue

    with session_scope() as session:
        job, _ = _locked(session, claim)
        _touch(job, state=('partial' if job.failed else 'succeeded'),
               message=(f'同步完成，{job.failed} 个商品规格未更新，已保留原数据'
                        if job.failed else '同步完成' + catalog_note), finished_at=now_beijing_naive())
