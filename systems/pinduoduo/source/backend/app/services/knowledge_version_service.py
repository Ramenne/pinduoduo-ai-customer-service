"""统一历史查询与恢复；恢复写原表并生成新版本，历史只读。"""
import json
from datetime import datetime
from decimal import Decimal
from sqlalchemy import select
from app.core import permission
from app.core.data_scope import ensure_shop_in_scope
from common.schemas.common import success_response,error_response
from common.models.knowledge_version_models import KnowledgeVersion
from common.models.user_models import SysUser
from common.services import knowledge_versions as versions


def access(session,user,kind,item_id,action='view'):
    model=versions.MODELS[kind];row=session.get(model,item_id)
    if row is None:return None,error_response(40400,'知识不存在')
    denied=ensure_shop_in_scope(session,row.shop_pk,user.id)
    if denied is not None:return None,denied
    if not permission.check(user,'product_knowledge' if kind=='product' else 'cs_knowledge',action,session=session):
        return None,error_response(40300,'无对应知识库权限')
    return row,None


def meta(session,row):
    actor=session.get(SysUser,row.created_by) if row.created_by else None
    return {'id':row.id,'version_no':row.version_no,'operation':row.operation,'actor':actor.username if actor else None,
        'actor_id':row.created_by,'at':row.created_at.isoformat(),'content_hash':row.content_hash,'source':row.source}


def current_view(session,kind,row):
    value=versions.dump(kind,row);digest=versions.digest(kind,row.shop_pk,row.id,value)
    # 普通读取不加写锁，也不虚构外部未留痕编辑所属的版本。
    last=session.scalar(select(KnowledgeVersion).where(KnowledgeVersion.kind==kind,KnowledgeVersion.item_id==row.id)
        .order_by(KnowledgeVersion.version_no.desc()).limit(1))
    return {'hash':digest,'values':value,'version_no':last.version_no if last and last.content_hash==digest else None}


def history(session,user,kind,item_id,page=1):
    row,denied=access(session,user,kind,item_id)
    if denied is not None:return denied
    revisions=list(session.scalars(select(KnowledgeVersion).where(KnowledgeVersion.kind==kind,
        KnowledgeVersion.item_id==item_id,KnowledgeVersion.shop_pk==row.shop_pk)
        .order_by(KnowledgeVersion.version_no.desc()).offset((page-1)*20).limit(21)))
    return success_response(data={'list':[meta(session,r) for r in revisions[:20]],'has_more':len(revisions)>20,
        'current':current_view(session,kind,row),'page':page})


def detail(session,user,kind,item_id,version_id):
    row,denied=access(session,user,kind,item_id)
    if denied is not None:return denied
    revision=session.get(KnowledgeVersion,version_id)
    if not revision or revision.kind!=kind or revision.item_id!=item_id or revision.shop_pk!=row.shop_pk:
        return error_response(40400,'该版本不属于此知识')
    values=json.loads(revision.payload_json)
    resource='product_knowledge' if kind=='product' else 'cs_knowledge'
    enabled=values.get('status')==1 if kind=='product' else values.get('enabled')
    can_restore=permission.check(user,resource,'update',session=session) and (enabled or permission.check(user,resource,'disable',session=session))
    return success_response(data={'version':meta(session,revision),'values':values,
        'current':current_view(session,kind,row),'can_restore':bool(can_restore)})


def restore(session,user,kind,item_id,version_id,expected_hash):
    row,denied=access(session,user,kind,item_id,'update')
    if denied is not None:return denied
    revision=session.get(KnowledgeVersion,version_id)
    if not revision or revision.kind!=kind or revision.item_id!=item_id or revision.shop_pk!=row.shop_pk:
        return error_response(40400,'该版本不属于此知识')
    values=json.loads(revision.payload_json)
    if not (values.get('status')==1 if kind=='product' else values.get('enabled')):
        if not permission.check(user,'product_knowledge' if kind=='product' else 'cs_knowledge','disable',session=session):
            return error_response(40300,'恢复停用版本需要停用权限')
    from common.models.shop_models import Shop
    owner=session.get(Shop,row.shop_pk).owner_user_id
    try:
        with versions.change(session,kind,row.shop_pk,user.id,'restore',item=row,source=f'version:{version_id}',expected_owner=owner) as edit:
            # 锁后的Shop是当前归属；再次复用原范围校验，阻断授权读取与取锁之间的换归属。
            if ensure_shop_in_scope(session,row.shop_pk,user.id) is not None:
                raise ValueError('店铺权限已变化，请刷新后重试')
            current_hash=versions.digest(kind,row.shop_pk,row.id,versions.dump(kind,row))
            if current_hash!=revision.content_hash and current_hash!=expected_hash:
                raise ValueError('当前知识已被修改，请重新查看后再恢复')
            if kind=='product' and values['goods_id']!=row.goods_id:
                raise ValueError('版本商品归属不一致，不可恢复')
            for key,value in values.items():
                if key=='goods_id':continue
                if kind=='product' and key in ('price','price_min','price_max') and value is not None:value=Decimal(str(value))
                if key=='last_extracted_at' and value is not None:value=datetime.fromisoformat(value)
                setattr(row,key,value)
        return success_response(data={'current':current_view(session,kind,row),'version':meta(session,edit.version)},message='已恢复为新的当前版本')
    except ValueError as exc:return error_response(40900,str(exc))
