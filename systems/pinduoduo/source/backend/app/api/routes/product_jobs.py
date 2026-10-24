"""商品后台同步及受保护的状态事件流；查看/订阅不会触发平台请求。"""
import asyncio
import json
import time

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_token, get_current_user
from app.api.routes.products import _ensure_permission, SyncProductsRequest
from app.core.errors import BusinessError
from app.services import product_jobs, product_service
from common.db.session import get_db, session_scope
from common.models.knowledge_models import Product
from common.models.product_sync_models import ProductSyncJob
from common.models.user_models import SysUser
from common.schemas.common import error_response, success_response

router = APIRouter(tags=['商品管理'])


def _enqueue(db, user, shop_pk, product_id=None):
    denied = _ensure_permission(user, 'update', db)
    if denied is not None:
        return denied
    shop, denied = product_service._resolve_shop_in_scope(db, user, shop_pk)
    if denied is not None:
        return denied
    if shop.status != 1:
        return error_response(40000, '店铺已停用，请先启用店铺')
    state = product_jobs.enqueue(db, shop, product_id=product_id,
                                 kind='specs' if product_id else 'catalog', actor_id=user.id)
    db.commit()  # 明确事务边界：后台执行只读取已经提交的任务。
    product_jobs.wake()
    return success_response(data=state, message='已加入后台同步')


@router.post('/products/sync-jobs')
def queue_catalog(payload: SyncProductsRequest, user: SysUser = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    return _enqueue(db, user, payload.shop_pk)


@router.post('/products/{product_id}/refresh')
def queue_specs(product_id: int, user: SysUser = Depends(get_current_user),
                db: Session = Depends(get_db)):
    product = db.get(Product, product_id)
    if product is None:
        return error_response(40400, '商品不存在')
    return _enqueue(db, user, product.shop_pk, product_id)


def read_status(token, shop_pk):
    with session_scope() as db:
        # 每次状态变化检查当前权限/令牌撤销/账号停用/店铺归属，连接不会永久保留旧权限。
        user = get_current_user(token, db)
        denied = _ensure_permission(user, 'view', db)
        if denied is not None:
            raise BusinessError(40300, '无商品查看权限')
        _, denied = product_service._resolve_shop_in_scope(db, user, shop_pk)
        if denied is not None:
            raise BusinessError(denied.code, denied.message)
        return product_jobs.snapshot(db.scalar(select(ProductSyncJob).where(
            ProductSyncJob.shop_pk == shop_pk)))


@router.get('/products/sync-status')
def sync_status(shop_pk: int = Query(..., gt=0), token: str = Depends(get_current_token)):
    return success_response(data=read_status(token, shop_pk))


@router.get('/products/sync-events')
async def sync_events(request: Request, shop_pk: int = Query(..., gt=0),
                      token: str = Depends(get_current_token)):
    initial = await asyncio.to_thread(read_status, token, shop_pk)

    async def events():
        last = None
        state = initial
        deadline = time.monotonic() + 60
        while True:
            if await request.is_disconnected():
                return
            if state != last:
                yield 'data: ' + json.dumps(state, ensure_ascii=False) + '\n\n'
                last = state
            if state['state'] not in product_jobs.ACTIVE:
                return
            if time.monotonic() >= deadline:
                return  # 客户端仅对活动任务重连，重连先读持久快照。
            yield ': keepalive\n\n'
            await asyncio.sleep(1)
            try:
                state = await asyncio.to_thread(read_status, token, shop_pk)
            except Exception:
                yield 'event: unavailable\ndata: {}\n\n'
                return

    return StreamingResponse(events(), media_type='text/event-stream', headers={
        'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})
