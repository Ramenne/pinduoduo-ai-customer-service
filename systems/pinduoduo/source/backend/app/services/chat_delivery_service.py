"""发送核对入口复用工作台权限和店铺归属，不提供重放外部请求的接口。"""
from sqlalchemy import select
from app.core.data_scope import ensure_shop_in_scope
from common.models.message_delivery_models import MessageDelivery
from common.services import message_delivery as ledger
from common.schemas.common import error_response, success_response


def deliveries(session, user, shop_pk, customer_uid):
    denied = ensure_shop_in_scope(session, shop_pk, user.id)
    if denied is not None:
        return denied
    rows = session.scalars(select(MessageDelivery).where(MessageDelivery.shop_pk == shop_pk,
        MessageDelivery.customer_uid == customer_uid,
        MessageDelivery.state.in_(['dispatching','uncertain','review'])
    ).order_by(MessageDelivery.id.desc()).limit(30))
    return success_response(data=[ledger.delivery_view(r) for r in rows])


def resolve(session, user, shop_pk, customer_uid, delivery_id, outcome, note):
    denied = ensure_shop_in_scope(session, shop_pk, user.id)
    if denied is not None:
        return denied
    try:
        result = ledger.resolve(session, shop_pk, customer_uid, delivery_id, outcome,
                                actor_id=user.id, note=note)
    except ValueError as exc:
        return error_response(40900, str(exc))
    return success_response(data=result)
