"""按实际字段识别扩展商品卡片，不猜测平台数字类型的含义。"""
import json


def goods_from_payload(message):
    def visit(value, depth=0):
        if depth > 4:
            return None
        if isinstance(value, str):
            if value.lstrip().startswith(('{', '[')):
                try: return visit(json.loads(value), depth+1)
                except (ValueError, TypeError): return None
            return None
        if isinstance(value, dict):
            gid = value.get('goods_id') or value.get('goodsId')
            if gid is not None and str(gid).strip():
                result = {'goods_id': str(gid)}
                for target, names in {
                    'goods_name': ('goods_name', 'goodsName', 'goods_title', 'goodsTitle', 'title'),
                    'thumb_url': ('thumb_url', 'thumbUrl', 'goods_thumb_url'),
                    'goods_price': ('goods_price', 'goodsPrice'),
                }.items():
                    for name in names:
                        item = value.get(name)
                        if isinstance(item, (str, int, float)):
                            result[target] = item
                            break
                return result
            for key in ('goods', 'goods_info', 'goodsInfo', 'content', 'info', 'data', 'mall_context'):
                if key in value:
                    found = visit(value[key], depth+1)
                    if found: return found
        elif isinstance(value, list):
            for item in value[:10]:
                found = visit(item, depth+1)
                if found: return found
        return None
    return visit(message)
