"""Everyday search navigation. Separate keywords and state from final checkout."""
import secrets
from .purchase_flow import label, search_results, search_candidates, PACKAGE, RID

BROWSE_KEYWORDS=(
    '收纳盒','收纳袋','桌面收纳','书架','置物架',
    '台灯','阅读灯','小夜灯','抱枕','坐垫',
    '马克杯','保温杯','玻璃杯','餐具','便当盒',
    '咖啡杯','咖啡滤杯','茶具','水壶','托盘',
    '笔记本','手账本','书签','贴纸','中性笔',
    '铅笔盒','文件夹','便签纸','胶带','剪刀',
    '帆布包','双肩包','零钱包','钥匙扣','雨伞',
    '手机壳','手机支架','平板支架','鼠标垫','理线器',
    '蓝牙音箱','耳机收纳盒','相框','花瓶','花盆',
    '桌布','杯垫','地垫','挂钩','衣架',
)


def begin(record):
    previous=record.get('browse_keyword')
    record.update(browse_keyword=secrets.choice([k for k in BROWSE_KEYWORDS if k!=previous]),
                  browse_search_at_viewed=len(record.get('viewed_items',[])),browse_search_pages=0,
                  browse_search_attempts=0,phase='browse_search_home')


def on_results(record,nodes):
    return bool(record.get('browse_keyword')) and search_results(nodes,record['browse_keyword'])


def cards(nodes):
    # Browsing is about content, with no ten-fen purchase constraint independent of purchase price ranking.
    found=search_candidates(nodes,budget_fen=None)
    return [{**c,'list_seller':'','list_identity_unique':False} for c in found]


def advance(record,snapshot,nodes,home):
    record['browse_search_attempts']=record.get('browse_search_attempts',0)+1
    if record['browse_search_attempts']>24:return None,None,'browse_page_not_confirmed'
    phase=record['phase']
    def tap(selector,after):
        return ({'action':'tap_unique_node','package':PACKAGE,'selector':selector},after,None) if selector else (None,None,None)
    if phase=='browse_search_home':
        if not home(snapshot):return {'action':'back'},phase,None
        selector=next(({'kind':'resource_id','value':RID+'search_bar_layout'} for n in nodes if n.get('resource_id')==RID+'search_bar_layout'),None)
        return tap(selector,'browse_search_input')
    if phase=='browse_search_input':
        fields=[n for n in nodes if n.get('class')=='android.widget.EditText' and n.get('editable') and not n.get('password')]
        if len(fields)==1 and label(nodes,'搜索'):
            return {'action':'set_node_text','package':PACKAGE,'selector':{'kind':'class','value':'android.widget.EditText'},'value':record['browse_keyword']},'browse_search_typed',None
    if phase=='browse_search_typed':
        if any(n.get('class')=='android.widget.EditText' and (n.get('text')==record['browse_keyword'] or n.get('description')==record['browse_keyword']) for n in nodes):
            return tap(label(nodes,'搜索'),'browse_search_results')
    if phase=='browse_search_results' and on_results(record,nodes):return None,'list',None
    return None,None,None
