"""网页候选审核接线；复用知识权限、店铺隔离及原知识表。"""
from sqlalchemy import select
from app.core import permission
from app.core.data_scope import ensure_shop_in_scope
from common.models.knowledge_curation_models import KnowledgeCuration
from common.models.knowledge_models import Product, ProductKnowledge, CustomerServiceKnowledge
from common.models.shop_models import Shop
from common.schemas.common import success_response, error_response
from common.services import knowledge_publication as publication


def access(session, user, shop_pk, category=None, action='view'):
    denied = ensure_shop_in_scope(session, shop_pk, user.id)
    if denied is not None:
        return denied
    if not permission.check(user, 'chat', 'view', session=session):
        return error_response(40300, '无人工贡献查看权限')
    if category and not permission.check(user, 'product_knowledge' if category == 'product' else 'cs_knowledge', action, session=session):
        return error_response(40300, '无对应知识库操作权限')


def task_access(session, user, task_id, category, action='view'):
    task = session.get(KnowledgeCuration, task_id)
    if task is None:
        return None, error_response(40400, '候选不存在')
    denied = access(session, user, task.shop_pk, category, action)
    if denied is not None:
        return None, denied
    shop = session.get(Shop, task.shop_pk)
    if task.owner_user_id != shop.owner_user_id:
        return None, error_response(40900, '店铺归属已变化，原候选不可入库')
    return task, None


def list_tasks(session, user, shop_pk, page=1):
    denied = access(session, user, shop_pk)
    if denied is not None:
        return denied
    shop = session.get(Shop, shop_pk)
    rows = list(session.scalars(select(KnowledgeCuration).where(KnowledgeCuration.shop_pk == shop_pk,
        KnowledgeCuration.owner_user_id == shop.owner_user_id, KnowledgeCuration.state == 'ready')
        .order_by(KnowledgeCuration.id.desc()).offset((page-1)*20).limit(21)))
    return success_response(data={'list': [{'id':r.id, 'state':r.state, 'candidates':publication.candidates_view(r)} for r in rows[:20]],
        'page':page, 'has_more':len(rows)>20})


def products(session, user, shop_pk, query=''):
    denied = access(session, user, shop_pk, 'product')
    if denied is not None:
        return denied
    q = select(Product).where(Product.shop_pk == shop_pk, Product.status == 1)
    if query:
        q = q.where(Product.goods_name.contains(query, autoescape=True) | Product.goods_id.contains(query, autoescape=True))
    rows = list(session.scalars(q.order_by(Product.id.desc()).limit(51)))
    return success_response(data={'list':[{'goods_id':r.goods_id,'goods_name':r.goods_name} for r in rows[:50]],'has_more':len(rows)>50})


def preview(session, user, task_id, index, category, title, goods_id=''):
    task, denied = task_access(session, user, task_id, category)
    if denied is not None:
        return denied
    try:
        return success_response(data=publication.preview(session, task_id, index, task.owner_user_id,
            category=category, title=title.strip(), goods_id=goods_id.strip()))
    except ValueError as exc:
        return error_response(40900, str(exc))


def review(session, user, task_id, index, payload):
    task, denied = task_access(session, user, task_id, payload['category'],
        'create' if payload['mode']=='create' else 'update')
    if denied is not None:
        return denied
    try:
        with session.begin_nested():
            result = publication.publish(session, task_id, index, task.owner_user_id, user.id, payload)
        result = dict(result)
        if result['state'] == 'published':
            model = ProductKnowledge if result['category'] == 'product' else CustomerServiceKnowledge
            current = session.get(model, result['target_id'])
            result['current_matches'] = bool(current and publication.fingerprint(publication.snapshot(current)) == result['after_hash'])
        return success_response(data=result)
    except publication.PublicationConflict as exc:
        return error_response(40900, str(exc))
    except ValueError as exc:
        return error_response(40000, str(exc))
