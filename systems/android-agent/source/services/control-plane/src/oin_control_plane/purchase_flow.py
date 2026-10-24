"""Measured search entry and bounded low-price selection. Never submits an order."""
import hashlib
import re
from .purchase_handoff import choose_keyword, price_fen, BUDGET_FEN, DEFAULT_KEYWORDS
import secrets

PACKAGE='com.taobao.idlefish'
RID=PACKAGE+':id/'
EXCLUDED=('互拍','互评','互好评','好.评','刷单','流量','鱼币','羊毛群','线报','代练','游戏','元宝','蝌蚪','引流','广告','拼团','小刀价','包邮费')


def label(nodes,value):
    for kind in ('description','text'):
        found=[n for n in nodes if n.get(kind)==value and n.get('enabled') is not False]
        if len(found)==1:return {'kind':kind,'value':value}
    return None


def search_results(nodes,keyword):
    return any(n.get('resource_id')==RID+'keyword_text' and n.get('text')==keyword for n in nodes) and label(nodes,'价格') is not None


def search_candidates(nodes, *, budget_fen=BUDGET_FEN):
    out=[]
    for node in nodes:
        raw=node.get('text') or node.get('description') or '';box=node.get('bounds',[])
        clean=re.sub('[\u200b\u200c\u200d\ufeff]','',raw)
        if not 10<=len(clean)<=500 or len(box)!=4 or box[1]<350 or box[3]<=box[1]:continue
        if any(word in clean for word in EXCLUDED):continue
        values=[]
        for other in nodes:
            b=other.get('bounds',[]);text=other.get('text') or other.get('description') or ''
            if len(b)!=4 or abs(b[0]-box[0])>35 or not 0<=b[1]-box[3]<150:continue
            match=re.fullmatch(r'(?:¥|￥)?(\d{1,7}(?:\.\d{1,2})?)元',text)
            fen=price_fen(match[1]) if match else None
            if fen is not None and 1<=fen and (budget_fen is None or fen<=budget_fen):values.append(fen)
        if not values:continue
        kind='text' if node.get('text') else 'description'
        selector=label(nodes,raw)
        if selector is None:continue
        key=hashlib.sha256(clean.encode()).hexdigest()[:24]
        out.append(dict(key=key,title=clean,list_price_fen=min(values),selector={'kind':kind,'value':raw}))
    return sorted(out,key=lambda item:item['list_price_fen'])


def retry_search(record):
    """Explicit resume starts another bounded search, retaining completed buyer work."""
    previous=record.get('search_keyword')
    keyword=secrets.choice([k for k in DEFAULT_KEYWORDS if k!=previous])
    for key in ('purchase_attempts','search_pages','purchase_seen','purchase_item',
                'purchase_detail_confirmed','purchase_handoff','next_parameters','after_action','purchase_back_attempts'):
        record.pop(key,None)
    record.update(phase='purchase_return_home',search_keyword=keyword)


def advance(record,snapshot,nodes,*,home,detail):
    """Returns (parameters, next_phase, error). Persisted intent is consumed by the durable channel."""
    phase=record['phase'];keyword=record.setdefault('search_keyword',choose_keyword(record.get('search_keyword')))
    record['purchase_attempts']=record.get('purchase_attempts',0)+1
    if record['purchase_attempts']>100:return None,None,'purchase_page_not_confirmed'
    def tap(selector,after):
        if selector is None:return None,None,None
        return {'action':'tap_unique_node','package':PACKAGE,'selector':selector},after,None
    if phase=='purchase_return_home':
        if home(snapshot):
            record['phase']='purchase_open'
            return advance(record,snapshot,nodes,home=home,detail=detail)
        record['purchase_back_attempts']=record.get('purchase_back_attempts',0)+1
        if record['purchase_back_attempts']>6:return None,None,'home_route_not_confirmed'
        return {'action':'back'},'purchase_return_home',None
    if phase=='purchase_open':
        if not home(snapshot):return None,None,'home_route_not_confirmed'
        return tap(label(nodes,RID+'search_bar_layout') or next(({'kind':'resource_id','value':RID+'search_bar_layout'} for n in nodes if n.get('resource_id')==RID+'search_bar_layout'),None),'purchase_input')
    if phase=='purchase_input':
        fields=[n for n in nodes if n.get('class')=='android.widget.EditText' and n.get('editable') and not n.get('password')]
        if len(fields)==1 and label(nodes,'搜索'):
            return {'action':'set_node_text','package':PACKAGE,'selector':{'kind':'class','value':'android.widget.EditText'},'value':keyword},'purchase_typed',None
    elif phase=='purchase_typed':
        if any(n.get('class')=='android.widget.EditText' and (n.get('text')==keyword or n.get('description')==keyword) for n in nodes):return tap(label(nodes,'搜索'),'purchase_results')
    elif phase=='purchase_results':
        if search_results(nodes,keyword):return tap(label(nodes,'价格'),'purchase_scan')
    elif phase=='purchase_scan':
        if not search_results(nodes,keyword):return None,None,None
        candidates=search_candidates(nodes)
        seen=record.setdefault('purchase_seen',[])
        for item in candidates:
            if item['key'] not in seen:
                seen.append(item['key']);record['purchase_item']=item
                return tap(item['selector'],'purchase_detail')
        record['search_pages']=record.get('search_pages',0)+1
        if record['search_pages']>=8:return None,None,'purchase_no_affordable_candidate'
        return {'action':'scroll_page','package':PACKAGE,'direction':'down'},'purchase_scan',None
    elif phase=='purchase_return_results':
        if search_results(nodes,keyword):
            record['phase']='purchase_scan'
            record.pop('purchase_back_attempts',None)
            return advance(record,snapshot,nodes,home=home,detail=detail)
        record['purchase_back_attempts']=record.get('purchase_back_attempts',0)+1
        if record['purchase_back_attempts']>4:return None,None,'purchase_page_not_confirmed'
        return {'action':'back'},'purchase_return_results',None
    elif phase=='purchase_detail':
        item=record['purchase_item'];data=detail(snapshot,item)
        if not data:return None,None,None
        price=re.match(r'[¥￥](\d+(?:\.\d{1,2})?)(?:，|,|$)',data['price'])
        fen=price_fen(price[1]) if price else None
        if fen is None or not 1<=fen<=BUDGET_FEN or fen!=item['list_price_fen'] or any(word in data['description'] for word in EXCLUDED):
            return {'action':'back'},'purchase_scan',None
        record['purchase_detail_confirmed']=True
        return tap(label(nodes,'立即购买按钮'),'purchase_checkout')
    elif phase=='purchase_checkout':
        # Terminal detection only. No selector for any order/payment button is ever emitted.
        labels=[n.get('text') or n.get('description') or '' for n in nodes]
        # Measured Android 11 native purchase sheet: closing control, shipping row,
        # selected Alipay and one combined confirmation label containing the actual total twice.
        totals=[]
        for value in labels:
            match=re.fullmatch(r'(确认购买|免密支付) (\d+(?:\.\d{1,2})?)，按钮, \1\n[¥￥](\d+(?:\.\d{1,2})?)',value)
            if match and match[2]==match[3]:totals.append(price_fen(match[2]))
        title=record.get('purchase_item',{}).get('title','')
        title_key=''.join(c for c in title if c.isalnum())[:10]
        identity=bool(title_key) and any(title_key in ''.join(c for c in value if c.isalnum()) for value in labels)
        # A virtual item need not have a freight row. The native closing control,
        # payment method and duplicated final payable amount identify this sheet;
        # absent freight alone never means free shipping or a zero total.
        checkout=label(nodes,'关闭页面') is not None and any(s.startswith('支付宝支付，已选择') for s in labels)
        # Rejecting an over-budget checkout only closes a page; it does not need the
        # stronger positive identity proof required to hand an eligible item to a human.
        if checkout and len(totals)==1 and totals[0] is not None and totals[0]>BUDGET_FEN:
            record.setdefault('purchase_rejected',[]).append({'key':record.get('purchase_item',{}).get('key'),'total_fen':totals[0],'reason':'total_over_budget'})
            record.pop('purchase_detail_confirmed',None)
            record['purchase_back_attempts']=0
            return tap(label(nodes,'关闭页面'),'purchase_return_results')
        if record.get('purchase_detail_confirmed') and checkout and identity and len(totals)==1:
            total=totals[0]
            if total is None or not 1<=total<=BUDGET_FEN:return None,None,'purchase_total_over_budget'
            record['purchase_handoff']={'state':'ready_for_human','budget_fen':BUDGET_FEN,'total_fen':total,'total_state':'confirmed','title':title}
            return None,'purchase_ready',None
        if any(s in {'立即支付','收银台','付款详情'} for s in labels):return None,None,'purchase_checkout_not_confirmed'
    return None,None,None
