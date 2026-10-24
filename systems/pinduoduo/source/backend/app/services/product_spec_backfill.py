"""兼容既有同步/调度入口，所有规格补拉进入同一个持久任务队列。"""
from app.services import product_jobs
from app.core.errors import BusinessError
from common.db.session import session_scope
from common.models.shop_models import Shop


def schedule_spec_backfill(shop_pk, shop_id, owner_user_id):
    with session_scope() as session:
        shop = session.get(Shop, shop_pk)
        if (shop is None or shop.shop_id != shop_id
                or shop.owner_user_id != owner_user_id):
            return False
        try:
            product_jobs.enqueue(session, shop, kind='specs')
        except BusinessError:
            return False
    product_jobs.wake()
    return True
