"""Content browsing from measured Android11 list/detail nodes; bounded collection and human purchase handoff."""
import hashlib
import secrets
import re
import unicodedata
from decimal import Decimal, InvalidOperation
from .security import now_seconds
from .store import TaskCapacityError
from . import browse_search

PACKAGE='com.taobao.idlefish'
RID=PACKAGE+':id/'


def normal(value):
    value=unicodedata.normalize('NFKC',str(value))
    return ''.join(c for c in value if c.isalnum() and c not in '\u200b\u200c\u200d\ufeff').lower()


def visible(snapshot):
    if not isinstance(snapshot,dict) or snapshot.get('package')!=PACKAGE or snapshot.get('truncated') or not isinstance(snapshot.get('nodes'),list):return []
    return [n for n in snapshot['nodes'] if isinstance(n,dict) and n.get('visible_to_user') is True and not n.get('password')]


def home(snapshot):
    ns=visible(snapshot)
    return any(n.get('text')=='推荐' or '推荐' in n.get('description','') for n in ns) and any(n.get('text')=='我的' or n.get('description','').startswith('我的，') for n in ns)


def amount_fen(value):
    match=re.fullmatch(r'[¥￥]?(\d{1,7}(?:\.\d{1,2})?)(万)?',str(value))
    if not match:return None
    try:return int(Decimal(match[1])*100*(10000 if match[2] else 1))
    except (InvalidOperation,ValueError):return None


def cards(snapshot):
    ns=visible(snapshot);out=[]
    if not home(snapshot):return out
    for n in ns:
        for kind in ['text','description']:
            raw=n.get(kind,'');title=re.sub(r'^(?:pic)+','',raw).strip()
            if len(title)<8 or len(raw)>240 or n.get('enabled') is False:continue
            if any(w in title for w in ['广告','现金签到','领现金','做任务','游戏中心']):continue
            box=n.get('bounds',[])
            if len(box)!=4 or box[2]<=box[0] or box[3]<=box[1]:continue
            # The selected title must sit beside an actual price in the same column.
            prices=[p for p in ns if p.get('text')=='¥' and len(p.get('bounds',[]))==4 and
                    abs(p['bounds'][0]-box[0])<70 and 0<=p['bounds'][1]-box[3]<200]
            if not prices:continue
            if sum(other.get(kind)==raw for other in ns)!=1:continue
            price_y=prices[0]['bounds'][1]
            parts=[p for p in ns if len(p.get('bounds',[]))==4 and
                box[0]-5<=p['bounds'][0]<box[0]+140 and abs(p['bounds'][1]-price_y)<35 and
                re.fullmatch(r'[¥\d.,万]+',p.get('text',''))]
            price=''.join(p.get('text','') for p in sorted(parts,key=lambda p:p['bounds'][0]))
            sellers=[p.get('text','') for p in ns if len(p.get('bounds',[]))==4 and
                25<=p['bounds'][0]-box[0]<=90 and 15<=p['bounds'][1]-price_y<=90 and
                2<=len(p.get('text',''))<=60 and not p.get('text','').startswith('pic') and
                not any(w in p.get('text','') for w in ['想要','卖家信用','发货','包邮'])]
            seller=sellers[0] if len(sellers)==1 else ''
            key=hashlib.sha256(normal(title).encode()).hexdigest()[:24]
            if not any(p['key']==key for p in out):out.append({'key':key,'title':title,'list_price':price,'list_price_fen':amount_fen(price),'list_seller':seller,'selector':{'kind':kind,'value':raw}})
    for item in out:item['list_identity_unique']=bool(item['list_seller']) and sum((p['list_seller'],p['list_price_fen'])==(item['list_seller'],item['list_price_fen']) for p in out)==1
    return sorted(out,key=lambda item: next(n['bounds'][1] for n in ns if n.get(item['selector']['kind'])==item['selector']['value']))


def detail(snapshot,selected,confirmed=False):
    ns=visible(snapshot)
    ids={n.get('resource_id','') for n in ns}
    if RID+'back_appbar' not in ids or RID+'detail_right_button' not in ids:return None
    bodies=[n.get('text','') for n in ns if len(n.get('text',''))>=16 and
            (not n.get('resource_id') or 'desc' in n.get('resource_id',''))]
    body=max(bodies,key=len,default='')
    service_titles=[n.get('text','') for n in ns if n.get('resource_id')==RID+'skill_title_tv']
    if service_titles:body='\n'.join(service_titles+[body])
    prices=[n.get('description','') or n.get('text','') for n in ns if n.get('resource_id')==RID+'detail_price_layout']
    sellers=[n.get('text','') for n in ns if n.get('resource_id')==RID+'user_info_nick']
    title_key=normal(selected['title'])[:10]
    price_match=re.match(r'[¥￥](\d+(?:\.\d{1,2})?)(?:，|,|$)',prices[0]) if prices else None
    seller_price_match=selected.get('list_identity_unique') is True and sellers and selected.get('list_seller')==sellers[0] and price_match and selected.get('list_price_fen')==amount_fen(price_match[1])
    if not confirmed and (not body or not prices or not sellers or not (title_key in normal(body) or seller_price_match)):return None
    return {'description':body,'price':prices[0] if prices else '', 'seller':sellers[0] if sellers else ''}


def pick_card(candidates):
    # Caller supplies only verified, visible, unseen cards. Persist the selection before dispatch.
    return secrets.choice(candidates) if candidates else None


def plan_reading(item):
    """One saved pause/direction at a time; resuming never redraws a pending plan."""
    if 'next_read_action_at' in item:return
    pause=5+secrets.randbelow(6)
    depth=item.get('scroll_depth',min(item.get('scrolls',0),3))
    direction='down' if depth<=0 else 'up' if depth>=3 else secrets.choice(('down','down','up'))
    item.update(reading_pause_seconds=pause,next_read_action_at=item['read_seconds']+pause,
                next_scroll_direction=direction,scroll_depth=depth)


def favorite_counter(snapshot):
    buttons=[n for n in visible(snapshot) if n.get('description')=='收藏按钮' and n.get('enabled') is not False]
    if len(buttons)!=1:return None
    count=buttons[0].get('favorite_count')
    return count if type(count) is int and 0<=count<=99999999 else None


async def advance(engine,job,step):
    ledger,io=engine.ledger,engine.io;owner,device=job['account_id'],job['device_id']
    import json
    record=json.loads(job['outcomes_json']).get(str(job['step_index']),{})
    if not record:
        record={'state':'current','phase':'list','viewed_items':[],'elapsed_seconds':0,'read_retries':0,'list_scrolls':0}
        if step['parameters'].get('search_browse'):browse_search.begin(record)
    async def change(**values):await io.call(ledger.change,job['id'],**values)
    async def save():await io.call(ledger.record_outcome,job,record)
    if job['task_id'] and record.get('processed_task_id')==job['task_id']:
        await change(task_id=None,probe_attempt=job['probe_attempt']+1,
            **({'status':'cancelled'} if job['control_state']=='stopping' else
               {'control_state':'paused'} if job['control_state']=='pausing' else {}));return
    async def paused(code):
        if record.get('after_action') in {'favorite_verify','favorite_restore_verify'}:
            record['phase']=record['after_action']
        record.pop('next_parameters',None);record.pop('pending_detail_scroll',None);record['last_read_at']=None;record['active_last_verified_at']=None;await save()
        await change(task_id=None,probe_attempt=job['probe_attempt']+1,control_state='paused',error_code=code)
    def action(parameters,after):record.update(next_parameters=parameters,after_action=after)
    if not job['task_id']:
        if job['control_state'] in {'pausing','stopping'}:
            await change(control_state='paused',**({'status':'cancelled'} if job['control_state']=='stopping' else {}));return
        if not await engine.connected(device):await paused('device_connection_interrupted');return
        params=record.get('next_parameters',{});await save()
        try:task,_=await io.call(ledger.phones.create_task,owner,device,'action' if params else 'observe',f"seller:{job['id']}:{job['step_index']}:{job['probe_attempt']}",params,60)
        except TaskCapacityError:return
        await change(task_id=task['id']);await engine.dispatch(task);return
    task=await io.call(ledger.phones.get_task,owner,device,job['task_id'])
    if task and task['status'] in {'queued','dispatched','running'}:return
    if job['control_state']=='stopping':await change(status='cancelled',task_id=None);return
    if not task or task['status']!='succeeded':await paused((task.get('error') or {}).get('code','browse_action_interrupted') if task else 'browse_action_interrupted');return
    record['processed_task_id']=task['id']
    result=task.get('result')
    if task['task_type']=='action':
        if not isinstance(result,dict) or result.get('success') is not True:await paused('browse_action_interrupted');return
        direction=record.pop('pending_detail_scroll',None)
        if direction:
            item=record['current_item'];item['scrolls']+=1
            item['scroll_depth']=max(0,min(3,item.get('scroll_depth',0)+(1 if direction=='down' else -1)))
            item.pop('next_read_action_at',None);item.pop('next_scroll_direction',None)
        record['phase']=record.pop('after_action',record['phase']);record.pop('next_parameters',None)
        record['read_retries']=0
    else:
        ns=visible(result)
        if not ns:
            record['last_read_at']=None;record['active_last_verified_at']=None
            record['read_retries']+=1
            if record['read_retries']>5:await paused('browse_page_not_confirmed');return
            await save();await change(task_id=None,probe_attempt=job['probe_attempt']+1,due_at=now_seconds()+3);return
        record['read_retries']=0
        from .seller import interruption_selector
        close=interruption_selector(result)
        if close:
            record['last_read_at']=None;record['active_last_verified_at']=None
            if record.get('close_attempts',0)>=3:await paused('browse_page_not_confirmed');return
            record['close_attempts']=record.get('close_attempts',0)+1
            action({'action':'tap_unique_node','package':PACKAGE,'selector':close},record['phase'])
        elif record['phase'] in {'list','returning','detail','favorite_verify','favorite_restore_verify'}:
            item=record.get('current_item')
            recognized=home(result) or browse_search.on_results(record,ns) or item and detail(result,item,item.get('content_confirmed',False))
            now=now_seconds()
            resumed=max((e['at'] for e in json.loads(job['outcomes_json']).get('_control_events',[]) if e.get('action')=='resume'),default=0)
            last=record.get('active_last_verified_at')
            delta=0 if not recognized or last is None or last<resumed else max(0,min(15,now-last))
            record['active_seconds']=record.get('active_seconds',record.get('elapsed_seconds',0))+delta
            record['active_last_verified_at']=now if recognized else None
        if close:
            pass  # The close intent was saved above; do not act on the covered page.
        elif record['phase'].startswith('browse_search_'):
            parameters,after,error=browse_search.advance(record,result,ns,home)
            if error:await paused(error);return
            if parameters:action(parameters,after)
            elif after:record['phase']=after
        elif record['phase'] in {'list','returning'}:
            search_list=browse_search.on_results(record,ns)
            if not (search_list if record.get('browse_keyword') else home(result)):
                record['route_retries']=record.get('route_retries',0)+1
                if record['route_retries']>4:await paused('home_route_not_confirmed');return
                action({'action':'back'},record['phase'])
            else:
                record['route_retries']=0
                if record['phase']=='returning':
                    item=record.pop('current_item',None)
                    if item and item.get('content_confirmed') and item.get('read_seconds',0)>=20 and item.get('scrolls',0)>=1:
                        record['viewed_items'].append({k:v for k,v in item.items() if k not in {'selector','last_read_at'}})
                    record['phase']='list';record['last_read_at']=None
                if record.get('active_seconds',record['elapsed_seconds'])>=step['parameters']['seconds'] and len(record['viewed_items'])>=3 and len(record.get('favorited_items',[]))>=step['parameters'].get('favorites',0):
                    if step['parameters'].get('purchase_handoff'):
                        record['phase']='purchase_return_home'
                        from .purchase_flow import advance as purchase_advance
                        parameters,after,error=purchase_advance(record,result,ns,home=home,detail=detail)
                        if error:await paused(error);return
                        if parameters:action(parameters,after)
                        await save();await change(task_id=None,probe_attempt=job['probe_attempt']+1);return
                    record.update(state='completed',note='已进入商品详情并完成内容浏览',finished_at=now_seconds());await save();return
                if record.get('browse_keyword') and len(record['viewed_items'])-record.get('browse_search_at_viewed',0)>=2:
                    browse_search.begin(record)
                    await save();await change(task_id=None,probe_attempt=job['probe_attempt']+1);return
                seen={p['key'] for p in record['viewed_items']}|set(record.get('skipped_items',[]));candidate=pick_card([c for c in (browse_search.cards(ns) if search_list else cards(result)) if c['key'] not in seen])
                if candidate:
                    record['current_item']={**candidate,'read_seconds':0,'scrolls':0,'content_confirmed':False}
                    action({'action':'tap_unique_node','package':PACKAGE,'selector':candidate['selector']},'detail')
                else:
                    record['list_scrolls']+=1
                    if record['list_scrolls']>40:await paused('browse_no_readable_items');return
                    if record.get('browse_keyword'):
                        record['browse_search_pages']=record.get('browse_search_pages',0)+1
                        if record['browse_search_pages']>=3:
                            browse_search.begin(record)
                            await save();await change(task_id=None,probe_attempt=job['probe_attempt']+1);return
                    action({'action':'scroll_page','package':PACKAGE,'direction':'down'},'list')
        elif record['phase'].startswith('purchase_'):
            from .purchase_flow import advance as purchase_advance
            parameters,after,error=purchase_advance(record,result,ns,home=home,detail=detail)
            if error:await paused(error);return
            if after=='purchase_ready':
                record.update(state='completed',note='预热完成，已停在购买确认页，请本人核对总价后下单',finished_at=now_seconds());await save();return
            if parameters:action(parameters,after)
        elif record['phase'] in {'favorite_verify','favorite_restore_verify'}:
            item=record['current_item']
            count=favorite_counter(result)
            if detail(result,item,True) is None:await paused('favorite_result_not_confirmed');return
            before=item['favorite_before']
            if record['phase']=='favorite_verify' and count==before+1:
                item['favorite_confirmed']=True
                record.setdefault('favorited_items',[]).append(item['key'])
                action({'action':'back'},'returning')
            elif record['phase']=='favorite_verify' and count==before-1:
                # It was already collected: restore that original state once, then select a different item.
                item['favorite_restore_attempted']=True
                action({'action':'tap_unique_node','package':PACKAGE,'selector':{'kind':'description','value':'收藏按钮'}},'favorite_restore_verify')
            elif record['phase']=='favorite_restore_verify' and count==before:
                item['favorite_already_saved']=True
                action({'action':'back'},'returning')
            else:
                record['favorite_retries']=record.get('favorite_retries',0)+1
                if record['favorite_retries']>=6:await paused('favorite_result_not_confirmed');return
        elif record['phase']=='detail':
            item=record['current_item'];data=detail(result,item,item.get('content_confirmed',False))
            if data is None:
                record['detail_retries']=record.get('detail_retries',0)+1
                if record['detail_retries']>=6:
                    skipped=record.setdefault('skipped_items',[])
                    if item['key'] not in skipped:skipped.append(item['key'])
                    if len(skipped)>=8:await paused('item_detail_not_confirmed');return
                    action({'action':'back'},'returning')
                    record['detail_retries']=0;record['last_read_at']=None
                    await save();await change(task_id=None,probe_attempt=job['probe_attempt']+1);return
                await save();await change(task_id=None,probe_attempt=job['probe_attempt']+1,due_at=now_seconds()+3);return
            record['detail_retries']=0
            for key,value in data.items():
                if value:item[key]=value
            plan_reading(item)
            now=now_seconds();resumed=max((e['at'] for e in json.loads(job['outcomes_json']).get('_control_events',[]) if e.get('action')=='resume'),default=0)
            last=record.get('last_read_at');elapsed=0 if last is None or last<resumed else max(0,min(15,now-last))
            item['read_seconds']+=elapsed;record['elapsed_seconds']+=elapsed;record['last_read_at']=now;item['content_confirmed']=True
            target=min(65,max(25,20+len(item.get('description',''))//12))
            if item['read_seconds']>=target and item['scrolls']>=1:
                if len(record.get('favorited_items',[]))<step['parameters'].get('favorites',0):
                    count=favorite_counter(result)
                    if count is None:
                        record['favorite_retries']=record.get('favorite_retries',0)+1
                        if record['favorite_retries']>=6:await paused('favorite_counter_unavailable');return
                    else:
                        item['favorite_before']=count;item['favorite_attempted']=True
                        record['favorite_retries']=0
                        action({'action':'tap_unique_node','package':PACKAGE,'selector':{'kind':'description','value':'收藏按钮'}},'favorite_verify')
                else:action({'action':'back'},'returning')
            elif item['read_seconds']>=item['next_read_action_at']:
                direction=item['next_scroll_direction'];record['pending_detail_scroll']=direction
                action({'action':'scroll_page','package':PACKAGE,'direction':direction},'detail')
            else:record['note']='正在查看商品内容'
    await save()
    await change(task_id=None,probe_attempt=job['probe_attempt']+1,due_at=now_seconds()+(0 if record.get('next_parameters') else 2 if record['phase']=='detail' else 4),control_state='paused' if job['control_state']=='pausing' else 'active')
