"""候选审核与既有知识同事务发布；result_json保留本次前后快照，不另建检索权威。"""
import hashlib
import json
from sqlalchemy import select, update
from common.models.knowledge_curation_models import KnowledgeCuration
from common.models.knowledge_models import ProductKnowledge, CustomerServiceKnowledge, Product
from common.models.shop_models import Shop
from common.services.contribution_policy import PRIVATE
from common.services import knowledge_versions as versions
from common.utils.time_utils import now_beijing_naive, safe_isoformat


class PublicationConflict(ValueError):
    pass


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def candidate_token(candidate):
    return fingerprint({k: v for k, v in candidate.items() if k not in ('publication', 'review', 'candidate_index', 'candidate_token')})


def candidates_view(row):
    return [{**c, 'candidate_index': i, 'candidate_token': candidate_token(c)}
        for i, c in enumerate(json.loads(row.result_json or '[]'))]


def snapshot(row):
    if row is None:
        return None
    product = isinstance(row, ProductKnowledge)
    return {'id': row.id, 'category': 'product' if product else 'service',
        'goods_id': row.goods_id if product else None,
        'title': row.goods_name if product else row.title,
        'content': (row.extracted_content if product else row.content) or '',
        'tags': None if product else row.tags,
        'enabled': row.status == 1 if product else bool(row.enabled)}


def load_candidate(session, task_id, index, owner_id, *, lock=False):
    query = select(KnowledgeCuration).where(KnowledgeCuration.id == task_id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    task = session.scalar(query)
    if not task or task.owner_user_id != owner_id:
        raise PublicationConflict('候选不存在或店铺归属已变化')
    if task.state != 'ready':
        raise PublicationConflict('提炼尚未完成，请刷新后重试')
    candidates = json.loads(task.result_json or '[]')
    if index < 0 or index >= len(candidates):
        raise PublicationConflict('候选不存在，请刷新后重试')
    return task, candidates, candidates[index]


def find_target(session, shop_pk, category, title, goods_id, *, lock=False):
    if category == 'product':
        if not goods_id:
            raise ValueError('请选择对应商品')
        query = select(ProductKnowledge).where(ProductKnowledge.shop_pk == shop_pk, ProductKnowledge.goods_id == goods_id)
    else:
        query = select(CustomerServiceKnowledge).where(CustomerServiceKnowledge.shop_pk == shop_pk, CustomerServiceKnowledge.title == title)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    model = ProductKnowledge if category == 'product' else CustomerServiceKnowledge
    enabled = model.status if category == 'product' else model.enabled
    rows = list(session.scalars(query.order_by(enabled.desc(), model.id).limit(2)))
    active = [row for row in rows if snapshot(row)['enabled']]
    if len(active) > 1:
        raise PublicationConflict('存在多个启用的同商品或同标题知识，请先在知识库保留一条启用记录后再入库')
    # 历史停用副本不阻塞唯一有效主体；全停用时仍返回停用目标，禁止悄悄复活。
    return active[0] if active else (rows[0] if rows else None)


def review_target(session, task, candidate, category, title, goods_id, *, lock=False):
    ref = candidate.get('corrected_reference')
    if not ref:
        return find_target(session, task.shop_pk, category, title, goods_id, lock=lock)
    expected = {'pdd_product_knowledge': ('product', ProductKnowledge),
        'pdd_customer_service_knowledge': ('service', CustomerServiceKnowledge)}.get(ref.get('source'))
    if not expected or expected[0] != category:
        raise PublicationConflict('纠正候选需要回到原知识库核对，不能误写其他知识')
    query = select(expected[1]).where(expected[1].id == ref['id'], expected[1].shop_pk == task.shop_pk)
    if lock: query = query.with_for_update().execution_options(populate_existing=True)
    target = session.scalar(query)
    if target is None:
        raise PublicationConflict('原知识已不存在，请人工核对，不会另建冲突知识')
    if category == 'product' and goods_id != target.goods_id:
        raise PublicationConflict('请选择被纠正知识对应的原商品')
    return target


def preview(session, task_id, index, owner_id, *, category, title, goods_id=''):
    task, _, candidate = load_candidate(session, task_id, index, owner_id)
    target = review_target(session, task, candidate, category, title, goods_id)
    value = snapshot(target)
    return {'candidate': {**candidate, 'candidate_token': candidate_token(candidate)},
        'target': value, 'target_hash': fingerprint(value), 'shop_pk': task.shop_pk}


def publish(session, task_id, index, owner_id, actor_id, payload):
    # 以Shop行串行化同店铺本通路发布，避免两个不同批次同时创建同一商品/标题知识。
    initial = session.get(KnowledgeCuration, task_id)
    if not initial:
        raise PublicationConflict('候选不存在')
    shop = session.scalar(select(Shop).where(Shop.id == initial.shop_pk).with_for_update().execution_options(populate_existing=True))
    if not shop or shop.owner_user_id != owner_id or shop.status != 1 or shop.merged_into_shop_pk is not None:
        raise PublicationConflict('店铺已停用或归属变化，知识未写入')
    task, candidates, candidate = load_candidate(session, task_id, index, owner_id, lock=True)
    command_hash = fingerprint(payload)
    # 响应丢失时同决定重放，不以候选已离开pending为失败，也不再次写知识。
    previous = candidate.get('publication') or candidate.get('review')
    if previous:
        if previous.get('command_hash') == command_hash:
            return previous
        raise PublicationConflict('这条候选已处理，请刷新；如需修改请进入正式知识库')
    if candidate_token(candidate) != payload['candidate_token']:
        raise PublicationConflict('候选已变化，请重新预览')
    category = payload['category']; title = payload['title'].strip(); content = payload['content'].strip()
    if category not in ('product', 'service'):
        raise ValueError('未知知识库')
    if not title or not content or len(title) > 100 or len(content) > 4000:
        raise ValueError('标题或正文为空或过长')
    if payload['decision'] != 'reject' and PRIVATE.search(title + '\n' + content):
        raise ValueError('请移除电话、订单号、收货地址等个人信息后再入库')
    if payload['decision'] == 'reject':
        decision = {'command_hash': command_hash, 'actor_id': actor_id, 'at': safe_isoformat(now_beijing_naive()), 'state': 'rejected'}
        candidate['status'] = 'rejected'; candidate['review'] = decision
    else:
        target = review_target(session, task, candidate, category, title, payload.get('goods_id') or '', lock=True)
        before = snapshot(target)
        if fingerprint(before) != payload['target_hash']:
            raise PublicationConflict('已有知识已变化，请重新预览后确认')
        if before and not before['enabled']:
            raise PublicationConflict('已有知识已停用，请先在知识库核对并启用，不会自动恢复')
        with versions.change(session,category,task.shop_pk,actor_id,'curation_publish',item=target,source=f'curation:{task_id}:{index}',expected_owner=owner_id) as edit:
            if target is None:
                if payload['mode'] != 'create':
                    raise PublicationConflict('目标知识不存在，请重新预览')
                if category == 'product':
                    product = session.scalar(select(Product).where(Product.shop_pk == task.shop_pk,
                        Product.goods_id == payload['goods_id'], Product.status == 1).limit(1))
                    if not product:
                        raise ValueError('请先选择本店已同步的商品')
                    target = ProductKnowledge(shop_pk=task.shop_pk, goods_id=product.goods_id,
                        goods_name=product.goods_name, extracted_content=content, status=1, created_by=actor_id)
                else:
                    target = CustomerServiceKnowledge(shop_pk=task.shop_pk, title=title, content=content,
                        tags='商户政策' if candidate.get('knowledge_type') == 'policy' else '人工沉淀', enabled=True, created_by=actor_id)
                session.add(target); session.flush(); action = 'created'
            else:
                if payload['mode'] not in ('append', 'replace'):
                    raise PublicationConflict('已有同商品或同标题知识，请选择合并或修订')
                old = before['content']
                combined = content if payload['mode'] == 'replace' else (old if content == old or content in old.split('\n\n') else '\n\n'.join(x for x in [old, content] if x))
                if len(combined) > 24000:
                    raise ValueError('合并后的知识过长，请整理后再入库')
                if combined == old:
                    action = 'reused'
                else:
                    model = ProductKnowledge if category == 'product' else CustomerServiceKnowledge
                    field = model.extracted_content if category == 'product' else model.content
                    predicates = [model.id == target.id, model.shop_pk == task.shop_pk,
                        field == (target.extracted_content if category == 'product' else target.content)]
                    predicates += [model.status == 1, model.goods_id == target.goods_id] if category == 'product' else [model.enabled.is_(True), model.title == target.title, model.tags == target.tags]
                    # 与旧CRUD并发编辑时也使用内容CAS，旧预览不能覆盖其后写入。
                    changed = session.execute(update(model).where(*predicates).values({field.key: combined, 'updated_at': now_beijing_naive()}).execution_options(synchronize_session=False))
                    if changed.rowcount != 1:
                        raise PublicationConflict('已有知识已被修改，请重新预览')
                    session.refresh(target); action = 'revised' if payload['mode'] == 'replace' else 'merged'
            edit.item=target
        after = snapshot(target)
        decision = {'command_hash': command_hash, 'actor_id': actor_id, 'at': safe_isoformat(now_beijing_naive()),
            'state': 'published', 'action': action, 'target_id': target.id, 'category': category,
            'before': before, 'after': after, 'after_hash': fingerprint(after),
            'knowledge_version_id': edit.version.id, 'knowledge_version_no': edit.version.version_no}
        candidate['status'] = 'published'; candidate['publication'] = decision
    task.result_json = json.dumps(candidates, ensure_ascii=False)
    session.flush()
    return decision
