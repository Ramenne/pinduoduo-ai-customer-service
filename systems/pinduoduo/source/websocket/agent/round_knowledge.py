"""同轮知识内容快照：复用现有店铺知识，不从人工补充自动发布知识。"""
import hashlib
import json
from dataclasses import dataclass, field
from types import SimpleNamespace
from sqlalchemy import select, func
from common.db.session import session_scope
from common.models.knowledge_models import Product, ProductKnowledge, CustomerServiceKnowledge
from common.services.kb_service import KbSearchResult, _tokenize, _match_token_count
from agent.product_catalog import describe_product
from agent.tools import format_search_result
from common.services import knowledge_versions as versions
from common.models.knowledge_version_models import KnowledgeVersion


class KnowledgeUnavailable(RuntimeError):
    pass


@dataclass
class RoundKnowledge:
    shop_pk: int
    products: tuple
    product_knowledge: tuple
    service_knowledge: tuple
    references: tuple
    used: set = field(default_factory=set)

    def catalog(self, query=''):
        rows=[r for r in self.products if not query or query.lower() in (r.goods_name or '').lower()]
        text=[f'【本店已同步商品目录】本轮快照共{len(rows)}件，最多展示12件。']
        self.used.update(('pdd_product',r.id) for r in rows[:12])
        text.extend(describe_product(r) for r in rows[:12])
        text.append('本地同步参考价格与目录不是实时库存/当前报价，不得据此承诺有货、物流或售后；缺实际业务依据请人工核实。')
        return '\n\n'.join(text)

    def execute(self, name, params):
        # shop_pk由宿主固定；模型不能切换店铺范围。
        if name=='list_shop_products':
            return self.catalog(str(params.get('query') or '')[:100])
        if name=='get_product_knowledge':
            goods=str(params.get('goods_id') or '')
            rows=[r for r in self.product_knowledge if r.goods_id==goods]
            if rows:
                self.used.update(('pdd_product_knowledge',r.id) for r in rows)
                return format_search_result(KbSearchResult(rows, []))
            product=next((r for r in self.products if r.goods_id==goods),None)
            if product:self.used.add(('pdd_product',product.id))
            return describe_product(product,details=True) if product else '本轮快照未找到该商品知识，请人工核对。'
        if name=='search_customer_service_knowledge':
            tokens=_tokenize(str(params.get('query') or ''))
            if not tokens:return '请提供具体知识查询词。'
            scored=[(r,_match_token_count(r,tokens)) for r in self.service_knowledge]
            rows=[r for r,s in sorted(scored,key=lambda x:(-x[1],x[0].id)) if s>0][:5]
            self.used.update(('pdd_customer_service_knowledge',r.id) for r in rows)
            return format_search_result(KbSearchResult([],rows))
        return '[错误：未知工具]'

    def evidence(self):
        return {'kind':'round_content_snapshot','shop_pk':self.shop_pk,
            'references':[r for r in self.references if (r['source'],r['id']) in self.used],
            'available_snapshot_count':len(self.references),
            'notice':'知识版本编号仅在与当前有效内容快照匹配时提供；未留痕旧记录为空。人工补充不自动入库。'}


def load_round_knowledge(shop_pk):
    """短事务内固定所有可供本轮工具读取的数据；超预算明确失败，不静默漏知识。"""
    specs=[(Product,('goods_id','goods_name','price','specifications','updated_at'),Product.status==1),
        (ProductKnowledge,(*versions.FIELDS['product'],'updated_at'),ProductKnowledge.status==1),
        (CustomerServiceKnowledge,(*versions.FIELDS['service'],'updated_at'),CustomerServiceKnowledge.enabled.is_(True))]
    sets=[];refs=[];size=0
    try:
        with session_scope() as session:
            latest_ids=select(func.max(KnowledgeVersion.id)).where(KnowledgeVersion.shop_pk==shop_pk).group_by(KnowledgeVersion.kind,KnowledgeVersion.item_id)
            # 版本正文不参与本轮检索；只读编号/hash，避免再载入一份完整知识。
            revisions={(v.kind,v.item_id):v for v in session.execute(select(
                KnowledgeVersion.id,KnowledgeVersion.kind,KnowledgeVersion.item_id,
                KnowledgeVersion.version_no,KnowledgeVersion.content_hash
            ).where(KnowledgeVersion.id.in_(latest_ids)))}
            for model,fields,enabled in specs:
                # 读取投影而非ORM全对象，限制单条数据库传输与总上下文。
                columns=[model.id,*[getattr(model,f) for f in fields]]
                rows=session.execute(select(*columns).where(model.shop_pk==shop_pk,enabled).order_by(model.id).limit(501)).all()
                if len(rows)>500:raise KnowledgeUnavailable('本店知识规模超过当前快照预算')
                frozen=[]
                for row in rows:
                    data=dict(zip(('id',*fields),row))
                    raw=json.dumps(data,ensure_ascii=False,default=str,sort_keys=True)
                    size+=len(raw.encode())
                    if size>512000:raise KnowledgeUnavailable('本店知识内容超过当前快照预算')
                    reference={'source':model.__tablename__,'id':row.id,'sha256':hashlib.sha256(raw.encode()).hexdigest(),
                        'content':json.loads(raw)}
                    item=SimpleNamespace(**data)
                    kind='product' if model is ProductKnowledge else 'service' if model is CustomerServiceKnowledge else None
                    if kind:
                        revision=revisions.get((kind,row.id))
                        matched=revision and revision.content_hash==versions.digest(kind,shop_pk,row.id,versions.dump(kind,item))
                        reference.update(knowledge_version_id=revision.id if matched else None,version_no=revision.version_no if matched else None)
                    refs.append(reference)
                    frozen.append(item)
                sets.append(tuple(frozen))
        return RoundKnowledge(shop_pk,*sets,tuple(refs))
    except KnowledgeUnavailable:raise
    except Exception as exc:raise KnowledgeUnavailable('本店知识暂不可读') from exc
