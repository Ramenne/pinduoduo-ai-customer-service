"""移植原OIN ContributionClassifier 的保守先排除规则；只准入候选，不批准知识。"""
import re

def classify(text):
    text = (text or '').strip()
    if not text:
        return None
    asks = ('请问您','请问你','麻烦您','麻烦你','请您','请你','您是说','你是说','能不能提供',
        '能否提供','请提供','您的订单号','你的订单号','方便告知','麻烦提供')
    if any(x in text for x in asks) or text.endswith(('？','?')):
        return None
    if any(x in text for x in ('这次','此次','本次','仅此一次','下不为例','破例','特批','单独给','例外','特殊处理','临时给')):
        return None
    if any(x in text for x in ('已退款','已补发','已发货','已改地址','已经退','已经补','已经给','已处理',
        '帮您改','帮你改','已提交','已经申请','已经操作','给您退','给你退','已经安排')):
        return None
    for kind, words in (
        ('policy', ('本店','我们店铺','店铺规定','全场','统一','包邮','满减','发货时效','货到付款','退换货政策','我们的政策')),
        ('procedure', ('步骤','流程','如何申请','怎么申请','申请方法','操作方法','先点击','进入','可以在','天内申请')),
        ('faq', ('材质','规格','保质期','成分','尺寸','容量','型号','适用','保存','冷藏','冷冻','产地','净含量','是什么做的'))):
        if any(x in text for x in words):
            return kind
    return None


PRIVATE = re.compile(r'(?<!\d)\d{7,}(?!\d)|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|(?:地址|收货人|姓名|身份证|订单号)\s*[:：][^\n，。;；]+')

def redact(text):
    return PRIVATE.sub('[已隐去个人信息]', text or '')
