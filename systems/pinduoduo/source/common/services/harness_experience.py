"""Shop-scoped reviewed runtime guidance; never customer/product knowledge or authority."""
import json
import hashlib
from sqlalchemy import select
from common.models.harness_archive_models import HarnessExperience as Experience
from common.models.shop_models import Shop
from common.services import context_archive
from common.services.contribution_policy import redact


def capture(session, pk, uid, source_key, category, title, observation, *, source=None, guidance='', trigger_terms=''):
    shop=session.get(Shop,pk)
    if not shop or shop.status!=1 or shop.merged_into_shop_pk is not None: raise ValueError('店铺状态已变化')
    source_key=hashlib.sha256(source_key.encode()).hexdigest()
    row=session.scalar(select(Experience).where(Experience.shop_pk==pk,Experience.source_key==source_key))
    if row: return row
    row=Experience(shop_pk=pk,owner_user_id=shop.owner_user_id,customer_uid=str(uid),source_key=source_key,
        category=category[:32],title=redact(title)[:200],observation=redact(observation)[:3000],
        guidance=redact(guidance)[:2000],trigger_terms=redact(trigger_terms)[:400],
        source_json=json.dumps(source or {},ensure_ascii=False))
    session.add(row);session.flush()
    return row


def view(row):
    return {k:getattr(row,k) for k in ('id','shop_pk','customer_uid','category','title','observation',
        'guidance','trigger_terms','state','version_no','validation_note')}


def review(session, pk, ident, expected_version, action, *, title, guidance, trigger_terms, validation_note, actor_id):
    shop=session.get(Shop,pk)
    if not shop or shop.status!=1 or shop.merged_into_shop_pk is not None:raise ValueError('店铺状态已变化')
    row=session.scalar(select(Experience).where(Experience.id==ident,Experience.shop_pk==pk,
        Experience.owner_user_id==shop.owner_user_id).with_for_update().execution_options(populate_existing=True))
    if not row: raise ValueError('经验不存在')
    if row.version_no!=expected_version: raise ValueError('经验版本已变化，请刷新后重试')
    if action not in ('save','publish','revoke'): raise ValueError('无效操作')
    if action=='publish' and (not guidance.strip() or not trigger_terms.strip() or not validation_note.strip()):
        raise ValueError('启用需要适用关键词、处理指引和验证说明')
    context_archive.put(session,pk,row.customer_uid,'experience_version',f'{row.id}:{row.version_no}',
        {**view(row),'source':json.loads(row.source_json),'review_actor':actor_id})
    row.title=redact(title).strip();row.guidance=redact(guidance).strip()
    row.trigger_terms=redact(trigger_terms).strip();row.validation_note=validation_note.strip()
    row.state={'save':'candidate','publish':'published','revoke':'revoked'}[action]
    row.version_no+=1
    session.flush()
    context_archive.put(session,pk,row.customer_uid,'experience_version',f'{row.id}:{row.version_no}',
        {**view(row),'source':json.loads(row.source_json),'review_actor':actor_id})
    return view(row)


def load(session, pk, query):
    shop=session.get(Shop,pk)
    if not shop or shop.status!=1: return []
    rows=session.scalars(select(Experience).where(Experience.shop_pk==pk,
        Experience.owner_user_id==shop.owner_user_id,Experience.state=='published').order_by(Experience.id.desc()))
    import re
    result=[];size=0
    for row in rows:
        terms=[w.strip() for w in re.split(r'[,，;；\n]',row.trigger_terms) if w.strip()]
        if not terms or not any(w.lower() in (query or '').lower() for w in terms): continue
        item={'id':row.id,'version':row.version_no,'title':row.title,'guidance':row.guidance,
            'scope':'本店内部行为指引，不能作为商品事实或发送授权'}
        size+=len(json.dumps(item,ensure_ascii=False))
        if size>6000: break
        result.append(item)
        if len(result)>=5: break
    return result


def validate_loaded(session,pk,items):
    if not items:return
    shop=session.get(Shop,pk)
    for item in items:
        row=session.scalar(select(Experience).where(Experience.id==item['id'],Experience.shop_pk==pk,
            Experience.owner_user_id==shop.owner_user_id).with_for_update().execution_options(populate_existing=True))
        if not row or row.state!='published' or row.version_no!=item['version']:
            raise ValueError('本轮使用的运行经验已修订或撤回，请重新分析')
