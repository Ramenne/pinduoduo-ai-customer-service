"""AI 使用本店已同步商品目录；本地目录不等于实时库存。"""
from sqlalchemy import select, func, or_
from common.db.session import session_scope
from common.models.knowledge_models import Product


def describe_product(row, *, details=False):
    lines = [f'商品名称：{row.goods_name or "未命名商品"}；商品ID：{row.goods_id}']
    if row.price is not None:
        lines.append(f'同步时参考价格：{row.price}元（以购买页当前规格价格为准）')
    if details and row.specifications:
        lines.append(f'已同步规格：{str(row.specifications)[:1600]}')
    if row.updated_at:
        lines.append(f'记录更新时间：{row.updated_at:%Y-%m-%d %H:%M:%S}')
    return '\n'.join(lines)


def list_shop_products(params):
    shop_id = params.get('shop_id')
    if shop_id in (None, '', 0):
        return '[错误：缺少店铺ID]'
    query = str(params.get('query') or '').strip()[:100]
    with session_scope() as session:
        conditions = [Product.shop_pk == int(shop_id), Product.status == 1,
            or_(Product.platform_on_sale.is_(None), Product.platform_on_sale == 1)]
        if query:
            conditions.append(Product.goods_name.contains(query, autoescape=True))
        total = session.scalar(select(func.count()).select_from(Product).where(*conditions)) or 0
        rows = session.scalars(select(Product).where(*conditions).order_by(Product.id).limit(12)).all()
        if not rows:
            return '本店本地目录没有匹配的已同步商品；这不代表店铺没有在售商品，需要人工核对或更新商品同步。'
        text = [f'【本店已同步商品目录】共{total}件，以下展示{len(rows)}件。']
        text.extend(describe_product(row) for row in rows)
        text.append('目录状态仅表示本地记录已启用，不是实时库存或当前上架状态；不得据此承诺有货、发货时效或售后政策。')
        text.append('客户说“这款”时结合会话商品信息和此目录按商品名称确认，不能声称看不到商品列表或反复索要链接。目录只有一条不等于店铺实际只有一款，禁止断言“店铺目前只有这一款”。同步参考价不是已核验的实时购买页价格。库存未知时请人工核实，不用“能下单一般就有货”推断库存。面对客户自然确认商品名称即可，不解释内部目录、同步或技术细节。')
        return '\n\n'.join(text)


def product_details(shop_id, goods_id):
    with session_scope() as session:
        row = session.scalar(select(Product).where(Product.shop_pk == int(shop_id),
            Product.goods_id == str(goods_id), Product.status == 1,
            or_(Product.platform_on_sale.is_(None), Product.platform_on_sale == 1)))
        if row is None:
            return '本店未找到该商品的已同步目录或知识，请人工核对。'
        return describe_product(row, details=True) + '\n以上为本地商品记录，未取得实时库存；商品知识和实际售后规则尚未补齐的内容需人工确认。'
