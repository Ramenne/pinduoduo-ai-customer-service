"""Durable free bulk brighten and all-listed-item collection on the phone channel."""
import hashlib
import json

from .product_metrics import extract
from .seller_page import brighten_status, confirmed_selling, selling_count, visible_texts
from .security import now_seconds
from .store import TaskCapacityError


async def advance(engine, job, step):
    ledger,io=engine.ledger,engine.io
    owner,device=job['account_id'],job['device_id']
    record=json.loads(job['outcomes_json']).get(str(job['step_index']),{})
    record={**record,'state':'current'}
    brighten=step['task_type']=='brighten_all'
    async def change(**fields):await io.call(ledger.change,job['id'],**fields)
    async def save():await io.call(ledger.record_outcome,job,record)
    async def end(state,note,**extra):
        await io.call(ledger.record_outcome,job,{**record,**extra,'state':state,'note':note,'finished_at':now_seconds()})
        if job['control_state']=='pausing':await change(control_state='paused')
        if job['control_state']=='stopping':await change(status='cancelled')
    if not job['task_id']:
        if job['control_state'] in {'pausing','stopping'}:
            await change(control_state='paused' if job['control_state']=='pausing' else 'stopping' if job['control_state']=='stopping' else 'active',
                         **({'status':'unknown' if record.get('mutation_attempted') and not record.get('confirmed') else 'cancelled'} if job['control_state']=='stopping' else {}));return
        if brighten and not record.get('mutation_attempted') and any(
                isinstance(value,dict) and value.get('state')=='completed' and value.get('product_metrics',{}).get('state')=='empty_goods'
                for value in json.loads(job['outcomes_json']).values()):
            await end('skipped','没有在售商品，无需擦亮',confirmed=True);return
        if not await engine.connected(device):await change(control_state='paused',error_code='device_connection_interrupted');return
        params=record.get('next_parameters',{})
        # Save intention before insertion; restart uses the same durable task key.
        if brighten and record.get('next_action_is_brighten'):
            record['mutation_attempted']=True;await save()
        try:
            task,_=await io.call(ledger.phones.create_task,owner,device,'action' if params else 'observe',
                f"seller:{job['id']}:{job['step_index']}:{job['probe_attempt']}",params,60)
        except TaskCapacityError:return
        await change(task_id=task['id']);await engine.dispatch(task);return
    task=await io.call(ledger.phones.get_task,owner,device,job['task_id'])
    if task and task['status'] in {'queued','dispatched','running'}:return
    uncertain=brighten and record.get('mutation_attempted') and not record.get('confirmed')
    if not task or task['status']!='succeeded':
        no_click=task and task['status'] in {'failed','rejected'} and (task.get('error') or {}).get('code') in {
            'bridge_paused','screen_off','device_locked','xianyu_not_foreground','system_ui_foreground','accessibility_unavailable'}
        if uncertain and no_click:
            record['mutation_attempted']=False;record.pop('next_action_is_brighten',None);uncertain=False
        if uncertain:await change(status='unknown',error_code='brighten_result_unknown');return
        record.pop('next_parameters',None);await save()
        await change(task_id=None,probe_attempt=job['probe_attempt']+1,
            **({'status':'cancelled'} if job['control_state']=='stopping' else {'control_state':'paused','error_code':'maintenance_page_interrupted'}));return
    result=task.get('result')
    if task['task_type']=='action':
        if not isinstance(result,dict) or result.get('success') is not True:
            await change(**({'status':'unknown','error_code':'brighten_result_unknown'} if uncertain else {'control_state':'paused','error_code':'maintenance_action_failed'}));return
        record.pop('next_parameters',None)
        if brighten and record.pop('next_action_is_brighten',False):record['phase']='verify'
        await save();await change(task_id=None,probe_attempt=job['probe_attempt']+1,
            control_state='paused' if job['control_state']=='pausing' else 'stopping' if job['control_state']=='stopping' else 'active');return
    if not visible_texts(result):
        if uncertain:await change(status='unknown',error_code='brighten_result_unknown');return
        await change(task_id=None,probe_attempt=job['probe_attempt']+1,control_state='paused',error_code='maintenance_page_interrupted');return
    from .seller import interruption_selector
    close=interruption_selector(result)
    if close and record.get('close_attempts',0)<2:
        record['next_parameters']={'action':'tap_unique_node','package':'com.taobao.idlefish','selector':close}
        record['close_attempts']=record.get('close_attempts',0)+1
        await save();await change(task_id=None,probe_attempt=job['probe_attempt']+1,
            control_state='paused' if job['control_state']=='pausing' else 'stopping' if job['control_state']=='stopping' else 'active');return
    if brighten:
        status=brighten_status(result)
        if status['state']=='already_done':
            await end('completed' if uncertain else 'skipped',
                '已完成一键擦亮（全部可擦亮在售商品）' if uncertain else '今日已一键擦亮，无需重复执行',confirmed=True);return
        if uncertain:
            if status['state']=='wrong_page' and not record.get('returned_from_paid') and any(
                    any(word in t for word in ['立即支付','确认支付','选择支付方式','超强擦亮','实付款']) for t in visible_texts(result)):
                record['next_parameters']={'action':'back'};record['returned_from_paid']=True
                await save();await change(task_id=None,probe_attempt=job['probe_attempt']+1,
                    control_state='paused' if job['control_state']=='pausing' else 'stopping' if job['control_state']=='stopping' else 'active');return
            record['verify_count']=record.get('verify_count',0)+1
            if record['verify_count']>=3:await change(status='unknown',error_code='brighten_result_unknown');return
        elif status['state']=='entry':
            record['entry_attempts']=record.get('entry_attempts',0)+1
            if record['entry_attempts']>3:
                await change(task_id=None,probe_attempt=job['probe_attempt']+1,control_state='paused',error_code='free_brighten_not_available');return
            record['next_parameters']=status['parameters']
            record['note']='进入普通一键擦亮页面'
        elif status['state']=='available':
            record['next_parameters']=status['parameters'];record['next_action_is_brighten']=True
            record['note']='执行普通一键擦亮，作用于全部可擦亮商品'
        else:
            await change(task_id=None,probe_attempt=job['probe_attempt']+1,control_state='paused',error_code='free_brighten_not_available');return
    else:
        if not confirmed_selling(result):
            await change(task_id=None,probe_attempt=job['probe_attempt']+1,control_state='paused',error_code='seller_list_not_confirmed');return
        count=selling_count(result)
        if 'expected_count' not in record:
            if count is None:await change(control_state='paused',error_code='seller_count_not_confirmed');return
            record['expected_count']=count
        elif count is not None and count!=record['expected_count']:
            await change(control_state='paused',error_code='seller_count_changed');return
        parsed=extract(result,task['result_received_at'] or now_seconds())
        products=record.setdefault('collected_products',{})
        for p in parsed['products']:products[p['key']]={**p,'source_task_id':task['id']}
        record.setdefault('source_task_ids',[]).append(task['id'])
        record['pages_read']=record.get('pages_read',0)+1
        fingerprint=hashlib.sha256('\n'.join(visible_texts(result)).encode()).hexdigest()
        record['repeated_pages']=record.get('repeated_pages',0)+1 if record.get('last_page')==fingerprint else 0
        record['last_page']=fingerprint;record['note']=f"已读取{len(products)}/{record['expected_count']}件在售商品"
        if len(products)==record['expected_count'] and (products or parsed['state']=='empty_goods'):
            stamp=max((p['captured_at'] for p in products.values()),default=task['result_received_at'] or now_seconds())
            await end('completed',record['note'],product_metrics={'state':'captured' if products else 'empty_goods',
                'products':list(products.values()),'captured_at':stamp,'complete':True,
                'source_task_ids':record['source_task_ids'],'source_task_id':task['id']});return
        if len(products)>record['expected_count'] or record['repeated_pages']>=2 or record['pages_read']>=50:
            await save();await change(task_id=None,probe_attempt=job['probe_attempt']+1,control_state='paused',error_code='seller_collection_incomplete');return
        record['next_parameters']={'action':'scroll_page','package':'com.taobao.idlefish','direction':'down'}
    await save();await change(task_id=None,probe_attempt=job['probe_attempt']+1,
        control_state='paused' if job['control_state']=='pausing' else 'stopping' if job['control_state']=='stopping' else 'active')
