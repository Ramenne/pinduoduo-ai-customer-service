"""所有知识写入口共用事务快照；不为历史再建一套有效知识。"""
import hashlib
import json
from contextlib import contextmanager
from types import SimpleNamespace
from decimal import Decimal
from datetime import datetime
from sqlalchemy import select
from common.models.knowledge_models import ProductKnowledge, CustomerServiceKnowledge
from common.models.knowledge_version_models import KnowledgeVersion
from common.models.shop_models import Shop

MODELS = {'product': ProductKnowledge, 'service': CustomerServiceKnowledge}
FIELDS = {
    'product': ('goods_id','goods_name','price','price_min','price_max','sold_quantity','thumb_url','specifications','extracted_content','last_extracted_at','status'),
    'service': ('title','content','tags','enabled'),
}


def dump(kind, row):
    values = {}
    for key in FIELDS[kind]:
        value = getattr(row, key)
        if value is not None and key in ('price','price_min','price_max'): value = format(Decimal(str(value)),'.2f')
        elif isinstance(value, Decimal): value = str(value)
        if isinstance(value, datetime): value = value.isoformat()
        values[key] = value
    return values


def digest(kind, shop_pk, item_id, values):
    return hashlib.sha256(json.dumps({'kind':kind,'shop_pk':shop_pk,'item_id':item_id,'values':values},
        ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def lock_shop(session, shop_pk):
    row = session.scalar(select(Shop).where(Shop.id==shop_pk).with_for_update().execution_options(populate_existing=True))
    if not row or row.merged_into_shop_pk is not None:
        raise ValueError('店铺已合并或不存在')
    return row


def latest(session, kind, item_id):
    return session.scalar(select(KnowledgeVersion).where(KnowledgeVersion.kind==kind,KnowledgeVersion.item_id==item_id)
        .order_by(KnowledgeVersion.version_no.desc()).limit(1).with_for_update())


def record(session, kind, row, actor_id, operation, source=None):
    values=dump(kind,row);current=latest(session,kind,row.id);content_hash=digest(kind,row.shop_pk,row.id,values)
    if current and current.content_hash==content_hash:
        return current
    version=KnowledgeVersion(shop_pk=row.shop_pk,kind=kind,item_id=row.id,
        version_no=current.version_no+1 if current else 1,content_hash=content_hash,
        payload_json=json.dumps(values,ensure_ascii=False),operation=operation,source=source,created_by=actor_id)
    session.add(version);session.flush();return version


@contextmanager
def change(session, kind, shop_pk, actor_id, operation, *, item=None, goods_id=None, source=None, expected_owner=None):
    # 统一锁顺序Shop→知识行；版本失败时原知识修改也回滚。
    with session.begin_nested():
        shop=lock_shop(session,shop_pk)
        if expected_owner is not None and shop.owner_user_id!=expected_owner:
            raise ValueError('店铺归属已变化，请刷新后重试')
        model=MODELS[kind]
        query=select(model).where(model.shop_pk==shop_pk)
        if item is not None: query=query.where(model.id==item.id)
        elif goods_id is not None: query=query.where(model.goods_id==goods_id)
        else: query=None
        existing=session.scalar(query.order_by(model.id).limit(1).with_for_update().execution_options(populate_existing=True)) if query is not None else None
        if item is not None and existing is None: raise ValueError('知识不存在或范围已变化')
        state=SimpleNamespace(item=existing,version=None)
        if existing is not None:
            current=latest(session,kind,existing.id)
            state.version=record(session,kind,existing,None,'baseline' if current is None else 'observed_change')
        yield state
        session.flush()
        if state.item is not None:
            state.version=record(session,kind,state.item,actor_id,operation,source)
