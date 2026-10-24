"""连续招呼只接待一次；复用持久收件和发送账，不按正文删除消息。"""
import json
import unicodedata
from sqlalchemy import select
from common.db.session import session_scope
from common.models.message_delivery_models import MessageInbox, MessageDelivery
from common.services.message_delivery import assert_lease


def pure_greeting(text):
    if not isinstance(text, str) or len(text) > 40:
        return False
    normalized = ''.join(c for c in unicodedata.normalize('NFKC', text).lower()
        if not c.isspace() and not unicodedata.category(c).startswith('P'))
    return normalized in {'你好', '您好', '你好呀', '你好啊', '您好呀', '哈喽', '嗨', 'hello', 'hi'}


def repeated_greeting(handle, shop_pk, uid, text, *, has_context=False):
    if handle is None or has_context or not pure_greeting(text):
        return False
    with session_scope() as s:
        current = assert_lease(s, handle, shop_pk, uid)
        previous = list(s.scalars(select(MessageInbox).where(
            MessageInbox.shop_pk == shop_pk, MessageInbox.customer_uid == str(uid),
            MessageInbox.id < current.id).order_by(MessageInbox.id.desc()).limit(20)))
        for row in previous:
            age = (current.created_at - row.created_at).total_seconds()
            if not 0 <= age <= 60:
                return False
            payload = json.loads(row.payload)
            if (not pure_greeting(payload.get('content')) or payload.get('goods_context')
                    or payload.get('order_context')):
                return False  # 新问题/商品/订单打断招呼段，不能吞掉下一轮咨询。
            sent = s.scalar(select(MessageDelivery.id).where(
                MessageDelivery.inbox_id == row.id, MessageDelivery.shop_pk == shop_pk,
                MessageDelivery.customer_uid == str(uid), MessageDelivery.kind == 'text',
                MessageDelivery.state.in_(['accepted', 'confirmed'])).limit(1))
            if sent is not None:
                return True
            if row.result != 'repeated_greeting':
                return False  # 前次失败或结果不明不能假定已接待。
    return False
