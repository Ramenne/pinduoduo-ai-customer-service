"""Read-only candidate decisions for the final human order handoff.

No navigation, order creation, messaging or payment. Live search/detail adapters
are connected only after their actual page evidence has been captured.
"""
from decimal import Decimal, InvalidOperation
import re
import secrets

# Final checkout search only; ordinary browsing remains unrestricted.
DEFAULT_KEYWORDS=(
    '情感咨询','聊天陪伴','倾听服务','情绪倾听','学习规划',
    '简历建议','穿搭建议','收纳建议','读书交流','英语口语交流',
    '电子手账','手账模板','记账模板','预算表格','日程模板',
    '周计划模板','月计划模板','学习计划模板','打卡表模板','待办清单模板',
    '读书笔记模板','会议记录模板','工作总结模板','简历模板','求职信模板',
    'PPT模板','Excel模板','Word模板','Notion模板','日历模板',
    '电子书签','手机壁纸','电脑壁纸','平板壁纸','头像设计',
    '原创头像','原创插画','原创表情包','电子贺卡','邀请函模板',
    '明信片设计','海报模板','菜单模板','名片模板','标签模板',
    '课程表模板','旅行计划模板','健身记录模板','饮水打卡模板','习惯记录模板',
)
# Already-running tasks retain their saved search term across this update.
LEGACY_KEYWORDS=('数据线','手机壳','收纳袋','贴纸','书签','文具','钥匙扣','小夹子')
BUDGET_FEN=10


def choose_keyword(saved=None, *, choose=secrets.choice):
    # Persist the returned keyword in the task before executing search. Retries
    # reuse it instead of making the same task a different search.
    if saved is not None:
        if saved not in DEFAULT_KEYWORDS+LEGACY_KEYWORDS:raise ValueError('unrecognized_saved_keyword')
        return saved
    return choose(DEFAULT_KEYWORDS)


def price_fen(value):
    if not isinstance(value,str) or not re.fullmatch(r'(?:¥|￥)?\s*\d{1,7}(?:\.\d{1,2})?',value):return None
    try:return int(Decimal(value.lstrip('¥￥').strip())*100)
    except (InvalidOperation,ValueError):return None


def candidates(snapshot, keyword):
    if not isinstance(snapshot,dict) or snapshot.get('truncated') or snapshot.get('success') is not True:return []
    page=snapshot.get('page',{});items=snapshot.get('items',{})
    if not isinstance(page,dict) or not isinstance(items,dict) or page.get('page')!='HOME' or page.get('rules_verified') is not True or items.get('rules_verified') is not True or items.get('truncated'):return []
    cards=items.get('cards',[])
    if not isinstance(cards,list):return []
    result=[];seen=set()
    for card in cards:
        if not isinstance(card,dict) or card.get('confidence')!='sample_rule_complete':continue
        title,seller,price=card.get('title'),card.get('seller'),price_fen(card.get('price'))
        if not isinstance(title,str) or not title or len(title)>120 or not isinstance(seller,str) or not seller or len(seller)>120 or price is None or price>BUDGET_FEN:continue
        ident=(title,seller,price)
        if ident in seen:continue
        seen.add(ident)
        result.append({'title':title,'seller':seller,'display_price_fen':price,'budget_fen':BUDGET_FEN,'keyword':keyword,
            'shipping_state':'needs_human_check','total_state':'not_confirmed'})
    return sorted(result,key=lambda item:item['display_price_fen'])[:10]


def detail_handoff(snapshot, candidate):
    if not isinstance(snapshot,dict) or snapshot.get('package')!='com.taobao.idlefish' or snapshot.get('truncated') or not isinstance(snapshot.get('nodes'),list):return None
    nodes=[n for n in snapshot['nodes'] if isinstance(n,dict) and n.get('visible_to_user') is True and not n.get('password')]
    labels=[str(n.get('text') or n.get('description') or '') for n in nodes]
    if any(s in {'现金签到','闲鱼币','做任务','游戏画布'} for s in labels):return None
    title=candidate.get('title','')
    if not title or sum(title in s.splitlines() for s in labels)!=1:return None
    # A matching title alone does not prove that a list tap entered details.
    if not any(s in {'我想要','立即购买'} for s in labels):return None
    price=candidate.get('display_price_fen')
    if type(price) is not int or not 0<=price<=BUDGET_FEN:return None
    current_prices=[price_fen(s) for s in labels if s.startswith(('¥','￥'))]
    if current_prices!=[price]:return None
    return {**candidate,'state':'ready_for_human','message':'已停在商品页，请专人核对运费后点击下单；程序不下单、不付款'}
