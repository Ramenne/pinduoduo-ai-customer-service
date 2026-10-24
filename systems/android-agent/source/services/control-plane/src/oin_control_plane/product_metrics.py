"""Candidate extraction from complete seller-page semantics and like-for-like trends."""
import hashlib
import re
from datetime import datetime
from zoneinfo import ZoneInfo
from .seller_page import confirmed_selling, visible_texts

EXPOSURE=re.compile(r'(?P<scope>今日|总|累计)?曝光(?:量)?\s*[:：]?\s*(?P<value>\d+(?:,\d{3})*)(?![\d.万亿])')
OTHER=re.compile(r'(?P<name>浏览|想要|咨询)(?:量|数)?\s*[:：]?\s*(?P<value>\d+)(?![\d.万亿])')


def extract(snapshot, captured_at):
    if not isinstance(snapshot,dict) or snapshot.get('package')!='com.taobao.idlefish' or snapshot.get('truncated') or not isinstance(snapshot.get('nodes'),list):
        return {'state':'unconfirmed','products':[],'captured_at':captured_at}
    texts=visible_texts(snapshot)
    if any('你还没有在卖的宝贝' in t for t in texts):return {'state':'empty_goods','products':[],'captured_at':captured_at}
    if not confirmed_selling(snapshot):return {'state':'unconfirmed','products':[],'captured_at':captured_at}
    products=[];seen=set();duplicates=set()
    for text in texts:
        lines=[s.strip() for s in text.splitlines() if s.strip()]
        if len(lines)<2:continue
        if lines[0]=='今日数据':continue  # Account aggregate is never a product.
        # Real Flutter cards prefix the title with a notice and the edit action.
        # Require one full metric line and the adjacent title inside that card.
        metric_indices=[i for i,line in enumerate(lines) if EXPOSURE.fullmatch(line)]
        if '编辑' in lines:
            if len(metric_indices)!=1 or metric_indices[0]<1:continue
            pos=metric_indices[0]
            if lines.index('编辑')>=pos-1:continue
            title=lines[pos-1];metric_lines=lines[pos:]
        else:title=lines[0];metric_lines=lines[1:]
        matches=list(EXPOSURE.finditer('\n'.join(metric_lines)));match=matches[0] if len(matches)==1 else None
        if not match or len(title)>120 or EXPOSURE.search(title) or title in {'在卖','草稿','已下架','数据','商品数据','商品列表','在卖商品列表','全部商品','我的商品'}:continue
        ident=hashlib.sha256(title.encode()).hexdigest()[:24]
        if ident in seen:duplicates.add(ident);continue
        seen.add(ident)
        scope='daily' if match['scope']=='今日' else 'cumulative' if match['scope'] in {'总','累计'} else 'unknown'
        products.append({'key':ident,'title':title,'exposure':int(match['value'].replace(',','')),'scope':scope,'captured_at':captured_at,
            **{m['name']:int(m['value']) for m in OTHER.finditer('\n'.join(metric_lines))}})
    products=[p for p in products if p['key'] not in duplicates]
    return {'state':'captured' if products else 'metrics_unavailable','products':products,'captured_at':captured_at}


def trends(snapshots):
    latest={};history={}
    for snapshot in sorted(snapshots,key=lambda s:s['captured_at'],reverse=True):
        for row in snapshot.get('products',[]):
            key=row['key'];history.setdefault(key,[]).append(row);latest.setdefault(key,row)
    rows=[];tz=ZoneInfo('Asia/Shanghai')
    for key,row in latest.items():
        result={**row,'trend':'baseline_needed','advice':'正在积累可比较的数据'}
        if row['scope']=='daily':
            current=datetime.fromtimestamp(row['captured_at'],tz)
            prior=next((v for v in history[key][1:] if v['scope']=='daily' and
                (current.date()-datetime.fromtimestamp(v['captured_at'],tz).date()).days==1 and
                abs((current.hour*60+current.minute)-(datetime.fromtimestamp(v['captured_at'],tz).hour*60+datetime.fromtimestamp(v['captured_at'],tz).minute))<=60),None)
            if prior:
                delta=row['exposure']-prior['exposure'];result.update(previous_exposure=prior['exposure'],change=delta,trend='down' if delta<0 else 'up' if delta>0 else 'flat',advice='曝光下降，建议检查并调整关键词、标题或主图' if delta<0 else '曝光未下降')
        elif row['scope']=='unknown':result.update(trend='scope_unknown',advice='已读取曝光，统计周期未确认，暂不判断下降')
        else:result.update(trend='baseline_needed',advice='累计曝光需积累每日增量，暂不直接比较下降')
        rows.append(result)
    return sorted(rows,key=lambda r:(r['trend']!='down',r['title']))
