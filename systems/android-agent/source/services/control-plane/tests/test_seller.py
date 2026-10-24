import asyncio
import json

import pytest

from conftest import account, controller_headers, enroll
from oin_control_plane.protocol import validate_task_request
from oin_control_plane.security import SecurityError
from oin_control_plane.seller import SellerEngine, matches, validate_steps


START = {"kind":"text","value":"测试店"}
END = [{"kind":"text","value":"操作已完成"},{"kind":"text","value":"测试商品"}]
PLAN = [
    {"task_type":"observe","parameters":{},"expect":[START]},
    {"task_type":"action","parameters":{"action":"click_unique_node","package":"com.taobao.idlefish","selector":{"kind":"description","value":"确认擦亮"}},"effect":"commit"},
    {"task_type":"observe","parameters":{},"expect":END},
]


@pytest.fixture
def seller(client,app):
    owner=account(client,"seller-test")
    device=enroll(client,owner["token"])["device"]["id"]
    # Isolated fixture version, not a claim of physical-device verification.
    with app.state.database.transaction(immediate=True) as c:
        c.execute("UPDATE devices SET app_version_code=35 WHERE id=?",(device,))
    headers=controller_headers(app,owner)
    base=f"/api/v1/devices/{device}/seller"
    recipe=client.post(base+'/recipes',headers=headers,json={"name":"真实节点配置的测试流程","kind":"brighten","steps":PLAN})
    assert recipe.status_code==201,recipe.text
    return owner["account"]["id"],device,headers,base,recipe.json()["id"]


class InlineIo:
    async def call(self,fn,*args,**kwargs): return fn(*args,**kwargs)


def make_engine(app, *, fail_commit=False):
    calls=[]
    async def connected(device): return True
    async def dispatch(task):
        calls.append(task)
        if task["task_type"]=='observe':
            values=[START,*END]
            result={"package":"com.taobao.idlefish","truncated":False,"nodes":[{"text":v["value"],"visible_to_user":True} for v in values]}
            status='succeeded'
        else:
            status='failed' if fail_commit else 'succeeded'
            result=None if fail_commit else {"success":True,"message":"node_click_dispatched"}
        app.state.store.complete_task(task['device_id'],task['id'],status,result,'local_executor_error' if fail_commit and status=='failed' else None,None)
    return SellerEngine(app.state.seller_store,InlineIo(),dispatch,connected),calls


def add_job(app,seller):
    owner,device,_,_,recipe=seller
    return app.state.seller_store.job(owner,device,{"recipe_id":recipe,"authorize":True,"idempotency_key":"seller-test-once"})


def test_routes_auth_csrf_and_page(client,app,seller):
    owner,device,headers,base,_=seller
    assert client.get(base).status_code==401
    assert client.post(base+'/items',headers={"Cookie":headers['Cookie']},json={}).status_code==403
    page=client.get(f'/devices/{device}/seller',headers=headers)
    assert page.status_code==200
    assert "script-src 'self'" in page.headers['content-security-policy']
    assert "style-src 'self' 'unsafe-inline'" in page.headers['content-security-policy']
    assert 'seller.js' in page.text
    assert '<h1>闲鱼助手</h1>' in page.text
    assert 'current-step' in page.text and 'current-progress' in page.text
    assert 'task-sheet' in page.text
    assert 'task-rows' in page.text
    assert 'id="workflow-choice"' in page.text
    assert 'id="task-center"' in page.text and 'id="execution"' in page.text
    assert 'seller.css' in page.text and 'oin-brand.svg' in page.text
    assert client.get('/seller.js').status_code==200
    assert client.get('/seller.css').status_code==200
    assert client.get('/oin-brand.svg').headers['content-type'].startswith('image/svg+xml')
    other=account(client,'seller-other')
    assert client.get(base,headers=controller_headers(app,other)).status_code==404


def test_multiline_input_preserved():
    text='  Ａ商品\n第二行  '
    _,params,_=validate_task_request('action',{'action':'set_node_text','package':'com.taobao.idlefish','selector':{'kind':'resource_id','value':'test:id/input'},'value':text},60)
    assert params['value']==text


def test_exact_selector_preserves_real_multiline_and_width():
    value='发闲置\n自己拍图卖·啥都能换钱'
    for action in ['click_unique_node','set_node_text']:
        supplied={'action':action,'package':'com.taobao.idlefish','selector':{'kind':'description','value':value}}
        if action=='set_node_text': supplied['value']='商品描述'
        _,params,_=validate_task_request('action',supplied,60)
        assert params['selector']['value']==value
    _,params,_=validate_task_request('action',{'action':'click_unique_node','package':'com.taobao.idlefish','selector':{'kind':'text','value':' Ａ商品 '}},60)
    assert params['selector']['value']==' Ａ商品 '


def test_bounded_tap_uses_exact_selector_without_client_coordinates():
    params={'action':'tap_unique_node','package':'com.taobao.idlefish','selector':{'kind':'description','value':'价格设置'}}
    assert validate_task_request('action',params,60)[1]==params
    with pytest.raises(SecurityError):
        validate_task_request('action',params|{'x':640,'y':1815},60)


def test_builtin_navigation_is_immutable_idempotent_and_never_publishes(client,app,seller):
    owner,device,headers,base,_=seller
    body={'builtin_id':'open_publish_form_v1','idempotency_key':'builtin-once-key'}
    first=client.post(base+'/builtin-jobs',headers=headers,json=body)
    assert first.status_code==201,first.text
    repeat=client.post(base+'/builtin-jobs',headers=headers,json=body)
    assert repeat.json()['id']==first.json()['id']
    recipe=[r for r in app.state.seller_store.inventory(owner,device)['recipes'] if r['id']==first.json()['recipe_id']][0]
    assert recipe['name']=='上新准备 · 打开发布表单'
    assert all(s['effect']!='commit' for s in recipe['steps'])
    assert recipe['steps'][-1]['expect']==[{'kind':'description','value':'发布, 发布'},{'kind':'description','value':'添加图片'}]
    assert client.post(base+'/builtin-jobs',headers=headers,json=body|{'steps':[]}).status_code==422
    assert client.post(base+'/builtin-jobs',headers=headers,json=body|{'builtin_id':'publish_now'}).status_code==422


def test_old_phone_cannot_receive_bounded_tap(client,app,seller):
    owner,device,headers,base,_=seller
    payload={'task_type':'action','parameters':{'action':'tap_unique_node','package':'com.taobao.idlefish','selector':{'kind':'description','value':'价格设置'}},'ttl_seconds':60,'idempotency_key':'tap-upgrade-test'}
    response=client.post(base.removesuffix('/seller')+'/tasks',headers=headers,json=payload)
    assert response.status_code==409,response.text
    with app.state.database.transaction(immediate=True) as c:
        c.execute('UPDATE devices SET app_version_code=36 WHERE id=?',(device,))
    assert client.post(base.removesuffix('/seller')+'/tasks',headers=headers,json=payload).status_code==201


def test_page_evidence_must_be_visible_unique_and_complete():
    snapshot={'package':'com.taobao.idlefish','nodes':[{'text':'测试店','visible_to_user':True}]}
    assert matches(snapshot,[START])
    assert not matches(snapshot|{'truncated':True},[START])
    assert not matches(snapshot|{'nodes':[{'text':'测试店','visible_to_user':False}]},[START])
    assert not matches(snapshot|{'nodes':snapshot['nodes']*2},[START])


def test_commit_requires_readback_and_authorization(client,app,seller):
    bad=PLAN[:-1]
    with pytest.raises(SecurityError): validate_steps(bad,'brighten')
    owner,device,headers,base,recipe=seller
    assert client.post(base+'/jobs',headers=headers,json={'recipe_id':recipe,'idempotency_key':'unapproved-job'}).status_code==422


def test_workflow_restores_and_confirms_without_repeat(app,seller):
    job=add_job(app,seller)
    engine,calls=make_engine(app)
    async def run():
        for _ in range(8): await engine.tick()
    asyncio.run(run())
    owner,device,_,_,_=seller
    assert app.state.seller_store.inspect(owner,device,job['id'])['status']=='succeeded'
    assert len(calls)==3
    assert sum(t['task_type']=='action' for t in calls)==1
    asyncio.run(engine.tick())
    assert len(calls)==3


def test_unknown_blocks_automatic_restart_until_explicit_resolution(app,seller):
    job=add_job(app,seller)
    engine,calls=make_engine(app,fail_commit=True)
    async def run():
        for _ in range(6): await engine.tick()
    asyncio.run(run())
    owner,device,_,_,recipe=seller
    assert app.state.seller_store.inspect(owner,device,job['id'])['status']=='unknown'
    app.state.seller_store.job(owner,device,{'recipe_id':recipe,'authorize':True,'idempotency_key':'second-after-unknown'})
    restored,new_calls=make_engine(app)
    asyncio.run(restored.tick())
    assert new_calls==[]
    # Ending an unknown task stops its future work; it does not certify the prior effect.
    stopped=app.state.seller_store.reconcile(owner,device,job['id'],{'outcome':'cancel'})
    assert stopped['status']=='cancelled'
    saved=json.loads(app.state.seller_store.read_job(job['id'])['outcomes_json'])
    assert saved['_control_events'][-1]['effect_outcome']=='unknown'


def test_insert_attachment_crash_reuses_same_task(app,seller,monkeypatch):
    add_job(app,seller)
    ledger=app.state.seller_store
    engine,calls=make_engine(app)
    job=ledger.runnable()[0]
    original=ledger.change
    def fail(ident,**fields):
        if fields.get('task_id'): raise RuntimeError('injected_crash')
        return original(ident,**fields)
    monkeypatch.setattr(ledger,'change',fail)
    with pytest.raises(RuntimeError): asyncio.run(engine.advance(job))
    with ledger.db.connect() as c:
        first=c.execute('SELECT id FROM device_tasks').fetchone()['id']
    monkeypatch.setattr(ledger,'change',original)
    asyncio.run(engine.advance(ledger.runnable()[0]))
    assert calls[0]['id']==first
    with ledger.db.connect() as c: assert c.execute('SELECT count(*) AS n FROM device_tasks').fetchone()['n']==1


def test_cancel_before_claim_has_zero_dispatch(app,seller):
    job=add_job(app,seller)
    ledger=app.state.seller_store
    stale=ledger.runnable()[0]
    owner,device,_,_,_=seller
    ledger.reconcile(owner,device,job['id'],{'outcome':'cancel'})
    engine,calls=make_engine(app)
    asyncio.run(engine.advance(stale))
    assert calls==[]


def test_schedule_is_daily_idempotent_no_catchup_burst(app,seller):
    owner,device,_,_,recipe=seller
    ledger=app.state.seller_store
    schedule=ledger.schedule(owner,device,{'recipe_id':recipe,'interval_seconds':86400})
    ledger.due_schedules();ledger.due_schedules()
    inventory=ledger.inventory(owner,device)
    assert len(inventory['jobs'])==1
    assert inventory['schedules'][0]['next_due_at']>schedule['next_due_at']
    with pytest.raises(SecurityError):ledger.schedule(owner,device,{'recipe_id':recipe,'interval_seconds':3600})


def test_legacy_phone_cannot_accept_new_actions(client,app,seller):
    owner,device,headers,base,recipe=seller
    with app.state.database.transaction(immediate=True) as c:c.execute('UPDATE devices SET app_version_code=34 WHERE id=?',(device,))
    response=client.post(base.removesuffix('/seller')+'/tasks',headers=headers,json={'task_type':'action','parameters':PLAN[1]['parameters'],'ttl_seconds':60,'idempotency_key':'old-phone-new-action'})
    assert response.status_code==409


@pytest.mark.parametrize('missing_start,missing_terminal',[(False,False),(True,False),(False,True)])
def test_selling_overview_follows_observed_pages_and_stops_without_evidence(app,seller,missing_start,missing_terminal):
    owner,device,_,_,_=seller
    with app.state.database.transaction(immediate=True) as c:
        c.execute('UPDATE devices SET app_version_code=38 WHERE id=?',(device,))
    ledger=app.state.seller_store
    job=ledger.builtin_job(owner,device,{'builtin_id':'open_selling_overview_v1','idempotency_key':'overview-real-path'})
    calls=[]
    stage=[0]
    async def connected(device): return True
    async def dispatch(task):
        calls.append(task)
        if task['task_type']=='action':
            if task['parameters']['action']=='tap_unique_node': stage[0]+=1
            result={'success':True,'verified':False}
        else:
            anchors=([{'text':'我的'}] if not missing_start else []) if stage[0]==0 else (
                [{'text':'我发布的'}] if stage[0]==1 else
                ([] if missing_terminal else [{'description':v} for v in ['在卖','草稿','已下架']]))
            result={'package':'com.taobao.idlefish','truncated':False,
                    'nodes':[n|{'visible_to_user':True} for n in anchors]}
        app.state.store.complete_task(device,task['id'],'succeeded',result,None,None)
    engine=SellerEngine(ledger,InlineIo(),dispatch,connected)
    async def run():
        for _ in range(35): await engine.tick()
    asyncio.run(run())
    final=ledger.inspect(owner,device,job['id'])
    assert final['status']==('failed' if missing_start or missing_terminal else 'succeeded')
    taps=[t['parameters']['selector']['value'] for t in calls if t['parameters'].get('action')=='tap_unique_node']
    assert taps==([] if missing_start else ['我的','我发布的'])
    assert not any(t['parameters'].get('action') in {'set_node_text','import_image'} for t in calls)
    if final['status']=='succeeded': assert final['step_index']==final['step_count']==6


def test_description_action_and_builtin_require_upgrade_and_exact_bound_item(client,app,seller):
    owner,device,headers,base,_=seller
    action={'action':'set_description_text','package':'com.taobao.idlefish','value':'  Ａ商品\n第二行  '}
    assert validate_task_request('action',action,60)[1]==action
    for bad in [action|{'selector':{'kind':'text','value':'anywhere'}},action|{'package':'other.app'},action|{'value':''}]:
        with pytest.raises(SecurityError):validate_task_request('action',bad,60)
    request={'task_type':'action','parameters':action,'ttl_seconds':60,'idempotency_key':'description-upgrade-gate'}
    assert client.post(base.removesuffix('/seller')+'/tasks',headers=headers,json=request).status_code==409
    body={'builtin_id':'prepare_description_v1','idempotency_key':'description-item-once'}
    assert client.post(base+'/builtin-jobs',headers=headers,json=body).status_code==409
    with app.state.database.transaction(immediate=True) as c:
        c.execute('UPDATE devices SET app_version_code=41 WHERE id=?',(device,))
    assert client.post(base+'/builtin-jobs',headers=headers,json=body).status_code==422
    item=client.post(base+'/items',headers=headers,json={'title':'test','description':action['value'],'price':'1','images':[]}).json()
    response=client.post(base+'/builtin-jobs',headers=headers,json=body|{'item_id':item['id']})
    assert response.status_code==201,response.text
    assert client.post(base+'/builtin-jobs',headers=headers,json=body|{'item_id':item['id']}).json()['id']==response.json()['id']
    with app.state.database.connect() as c:
        row=c.execute('SELECT steps_json FROM seller_jobs WHERE id=?',(response.json()['id'],)).fetchone()
    steps=json.loads(row['steps_json'])
    assert [s['parameters']['value'] for s in steps if s['parameters'].get('action')=='set_description_text']==[action['value']]
    assert all(s['effect']!='commit' for s in steps)
    assert response.json()['kind']=='research' and response.json()['item_id']==item['id']
    assert client.post(base+'/builtin-jobs',headers=headers,json=body|{'item_id':'foreign-item','idempotency_key':'foreign-item-key'}).status_code==404


@pytest.mark.parametrize('mode,expected', [('verified','succeeded'),('unverified','unknown'),('inactive','failed'),('paused','running'),('uncertain_failure','unknown')])
def test_description_workflow_needs_ime_readback_not_just_dispatch(app,seller,mode,expected):
    owner,device,_,_,_=seller;ledger=app.state.seller_store
    with app.state.database.transaction(immediate=True) as c:
        c.execute('UPDATE devices SET app_version_code=41 WHERE id=?',(device,))
    item=ledger.item(owner,device,{'title':'test','description':'exact body','price':'1','images':[]})
    job=ledger.builtin_job(owner,device,{'builtin_id':'prepare_description_v1','item_id':item['id'],'idempotency_key':'description-evidence'})
    async def connected(device):return True
    async def dispatch(task):
        if task['parameters'].get('action')=='set_description_text' and mode in {'inactive','paused','uncertain_failure'}:
            code={'inactive':'oin_input_method_not_active','paused':'bridge_paused','uncertain_failure':'description_input_result_unverified'}[mode]
            app.state.store.complete_task(device,task['id'],'failed',None,code,None)
            return
        if task['task_type']=='observe':
            # Only tests the orchestration gate; this is not a phone/IME acceptance sample.
            result={'package':'com.taobao.idlefish','truncated':False,'nodes':[
                {'description':v,'visible_to_user':True} for v in ['卖闲置','发闲置\n自己拍图卖·啥都能换钱','发布, 发布','添加图片','完成']]}
        else: result={'success':True,'verified':mode=='verified' if task['parameters'].get('action')=='set_description_text' else False}
        app.state.store.complete_task(device,task['id'],'succeeded',result,None,None)
    engine=SellerEngine(ledger,InlineIo(),dispatch,connected)
    async def run():
        for _ in range(30):await engine.tick()
    asyncio.run(run())
    final=ledger.inspect(owner,device,job['id'])
    assert final['status']==expected
    if mode!='verified':assert final['step_index']==8
    if mode=='unverified':assert final['error_code']=='description_result_not_verified'
    if mode=='paused':assert final['control_state']=='paused' and not ledger.runnable()
    if expected=='unknown':
        assert ledger.runnable()==[]


def test_task_pause_resume_stop_and_durable_step_receipts(app,seller):
    owner,device,_,_,_=seller;ledger=app.state.seller_store
    job=add_job(app,seller);engine,calls=make_engine(app)
    async def run():
        await engine.control(owner,device,job['id'],'pause')
        await engine.tick();assert not calls
        assert ledger.inspect(owner,device,job['id'])['control_state']=='paused'
        await engine.control(owner,device,job['id'],'resume')
        await engine.tick()
        before=ledger.inspect(owner,device,job['id'])
        assert before['step_index']==0 and len(calls)==1
        await engine.control(owner,device,job['id'],'pause')
        await engine.tick()
        after=ledger.inspect(owner,device,job['id'])
        assert after['step_index']==1 and after['control_state']=='paused' and len(calls)==1
        assert after['steps'][0]['receipts'][0]['status']=='succeeded'
        assert after['steps'][0]['receipts'][0]['evidence']
        # A restart retains the exact next step and does not auto-resume.
        restarted,_=make_engine(app);await restarted.tick();assert len(calls)==1
        await engine.control(owner,device,job['id'],'stop');await engine.tick()
        assert ledger.inspect(owner,device,job['id'])['status']=='cancelled'
        assert len(calls)==1
    asyncio.run(run())


def test_stop_inflight_waits_receipt_and_never_starts_next_step(app,seller):
    owner,device,_,_,_=seller;ledger=app.state.seller_store;job=add_job(app,seller);calls=[]
    async def connected(device):return True
    async def dispatch(task):calls.append(task)
    engine=SellerEngine(ledger,InlineIo(),dispatch,connected)
    async def run():
        await engine.tick();assert len(calls)==1
        await engine.control(owner,device,job['id'],'stop')
        await engine.tick();assert ledger.inspect(owner,device,job['id'])['control_state']=='stopping'
        task=calls[0];app.state.store.complete_task(device,task['id'],'succeeded',{'package':'com.taobao.idlefish','nodes':[]},None,None)
        await engine.tick();assert ledger.inspect(owner,device,job['id'])['status']=='cancelled'
        await engine.tick();assert len(calls)==1
    asyncio.run(run())


def test_wait_pause_preserves_remaining_and_optional_action_is_recorded(app,seller,monkeypatch):
    owner,device,_,_,_=seller;ledger=app.state.seller_store
    clock=[2000000000];monkeypatch.setattr('oin_control_plane.seller.now_seconds',lambda:clock[0])
    plan=[{'task_type':'observe','parameters':{},'expect':[START]}, {'task_type':'wait','parameters':{'seconds':30},'name':'阅读商品30秒'}, {'task_type':'action','parameters':{'action':'tap_unique_node','package':'com.taobao.idlefish','selector':{'kind':'text','value':'今日签到'}},'effect':'navigation','when':{'kind':'text','value':'今日签到'}}, {'task_type':'observe','parameters':{},'expect':[START]}]
    recipe=ledger.recipe(owner,device,{'name':'带停留的真实任务','kind':'research','steps':plan})
    job=ledger.job(owner,device,{'recipe_id':recipe['id'],'idempotency_key':'wait-and-conditional'})
    engine,calls=make_engine(app)
    async def run():
        await engine.tick();await engine.tick();await engine.tick()
        assert ledger.inspect(owner,device,job['id'])['step_index']==1
        clock[0]+=10;await engine.control(owner,device,job['id'],'pause');await engine.tick()
        count=len(calls);clock[0]+=100;await engine.tick();assert len(calls)==count
        await engine.control(owner,device,job['id'],'resume');await engine.tick()
        clock[0]+=19;await engine.tick();assert ledger.inspect(owner,device,job['id'])['step_index']==1
        clock[0]+=1
        for _ in range(4):await engine.tick()
        detail=ledger.inspect(owner,device,job['id']);assert detail['steps'][2]['state']=='skipped'
        assert '今日签到' in detail['steps'][2]['note']
        assert not any(t['parameters'].get('action')=='tap_unique_node' for t in calls)
    asyncio.run(run())


def test_identifiable_ad_closure_requires_unique_complete_xianyu_snapshot():
    from oin_control_plane.seller import interruption_selector
    node={"text":"×","visible_to_user":True,"enabled":True}
    snapshot={"package":"com.taobao.idlefish","nodes":[node,{"text":"广告","visible_to_user":True,"enabled":True}]}
    assert interruption_selector(snapshot)=={"kind":"text","value":"×"}
    assert interruption_selector({**snapshot,"truncated":True}) is None
    assert interruption_selector({**snapshot,"package":"com.android.systemui"}) is None
    assert interruption_selector({**snapshot,"nodes":[node,node,snapshot["nodes"][1]]}) is None
    assert interruption_selector({**snapshot,"nodes":[node]}) is None
    assert interruption_selector({**snapshot,"nodes":[*snapshot["nodes"],{"text":"发布, 发布","visible_to_user":True,"enabled":True}]}) is None


def test_ad_close_reobserves_before_advancing(app,seller):
    owner,device,_,_,_=seller
    plan=[{"task_type":"observe","parameters":{},"expect":[START]}]
    recipe=app.state.seller_store.recipe(owner,device,{"name":"关闭广告后校验","kind":"research","steps":plan})
    with app.state.database.transaction(immediate=True) as c:c.execute("UPDATE devices SET app_version_code=48 WHERE id=?",(device,))
    job=app.state.seller_store.job(owner,device,{"recipe_id":recipe["id"],"idempotency_key":"ad-reobserve"})
    calls=[]
    async def connected(device):return True
    async def dispatch(task):
        calls.append(task)
        if task["task_type"]=="action":result={"success":True}
        elif len(calls)==1:result={"package":"com.taobao.idlefish","nodes":[{"text":"广告","visible_to_user":True,"enabled":True},{"text":"关闭广告","visible_to_user":True,"enabled":True}]}
        else:result={"package":"com.taobao.idlefish","nodes":[{"text":START["value"],"visible_to_user":True}]}
        app.state.store.complete_task(device,task["id"],"succeeded",result,None,None)
    engine=SellerEngine(app.state.seller_store,InlineIo(),dispatch,connected)
    for _ in range(8):asyncio.run(engine.tick())
    assert [t["task_type"] for t in calls]==["observe","action","observe"]
    assert calls[1]["parameters"]["selector"]["value"]=="关闭广告"
    assert app.state.seller_store.inventory(owner,device)["jobs"][0]["status"]=="succeeded"


def test_manual_resume_requires_explicit_confirmation_and_records_origin(app,seller):
    from oin_control_plane.store import ConflictError
    owner,device,_,_,_=seller;ledger=app.state.seller_store
    job=add_job(app,seller)
    plan=[{'task_type':'manual','parameters':{},'effect':'navigation','name':'本人确认'}]
    with app.state.database.transaction(immediate=True) as c:
        c.execute("UPDATE seller_jobs SET steps_json=?,status='running',control_state='paused' WHERE id=?",(json.dumps(plan),job['id']))
    with pytest.raises(ConflictError,match='manual_completion_confirmation_required'):
        ledger.control(owner,device,job['id'],'resume',origin='bound_device')
    ledger.control(owner,device,job['id'],'resume',confirm_manual=True,origin='bound_device')
    detail=ledger.inspect(owner,device,job['id'])
    assert detail['control_events'][-1]['origin']=='bound_device'
    assert detail['steps'][0]['state']=='completed'


def test_today_brightened_checkpoint_continues_without_any_click(app,seller):
    from test_product_metrics import populated_phone_page
    owner,device,_,_,_=seller;ledger=app.state.seller_store
    plan=[{'task_type':'observe','parameters':{},'effect':'read','expect':[
        {'kind':'description','value':v} for v in ['在卖','草稿','已下架']]},
        {'task_type':'manual','parameters':{},'effect':'navigation',
         'name':'完成今日擦亮并确认继续（有商品时的擦亮节点仍需实页接通）'},
        {'task_type':'report','parameters':{},'effect':'read','name':'整理卖家数据，提醒人工优化与上新'}]
    recipe=ledger.recipe(owner,device,{'name':'已擦亮手机样本','kind':'metrics','steps':plan})
    job=ledger.job(owner,device,{'recipe_id':recipe['id'],'idempotency_key':'already-brightened-checkpoint'})
    calls=[]
    async def connected(device):return True
    async def dispatch(task):
        calls.append(task)
        app.state.store.complete_task(device,task['id'],'succeeded',populated_phone_page(),None,None)
    engine=SellerEngine(ledger,InlineIo(),dispatch,connected)
    for _ in range(6):asyncio.run(engine.tick())
    detail=ledger.inspect(owner,device,job['id'])
    assert detail['status']=='succeeded'
    assert detail['steps'][1]['state']=='skipped'
    assert '未重复擦亮' in detail['steps'][1]['note']
    assert [t['task_type'] for t in calls]==['observe']
    assert ledger.product_metrics(owner,device)['products'], detail['steps']
    assert ledger.product_metrics(owner,device)['products'][0]['title']=='情感咨询'


def test_cash_game_entries_cannot_be_started_even_from_legacy_workflows(app,seller):
    from oin_control_plane.seller_workflows import BUILTINS
    for ident in ['daily_preheat_v1','daily_maintenance_v1']:
        with pytest.raises(SecurityError,match='reward_game_entry_disabled'):
            validate_steps(BUILTINS[ident]['steps'],BUILTINS[ident]['kind'])
    for ident in ['daily_preheat_v2','daily_maintenance_v2']:
        validate_steps(BUILTINS[ident]['steps'],BUILTINS[ident]['kind'])


def test_interrupted_safe_navigation_preserves_step_until_explicit_resume(app,seller):
    owner,device,_,_,_=seller;ledger=app.state.seller_store
    job=add_job(app,seller);calls=[]
    async def connected(device):return True
    async def dispatch(task):
        calls.append(task)
        app.state.store.complete_task(device,task['id'],'succeeded',{'package':'com.android.systemui','nodes':[]},None,None)
    engine=SellerEngine(ledger,InlineIo(),dispatch,connected)
    for _ in range(3):asyncio.run(engine.tick())
    paused=ledger.inspect(owner,device,job['id'])
    assert paused['control_state']=='paused' and paused['step_index']==0 and len(calls)==1
    asyncio.run(engine.tick());assert len(calls)==1


def test_disconnected_job_does_not_auto_restart_on_reconnect(app,seller):
    owner,device,_,_,_=seller;ledger=app.state.seller_store
    job=add_job(app,seller);connected_now=False;calls=[]
    async def connected(device):return connected_now
    async def dispatch(task):calls.append(task)
    engine=SellerEngine(ledger,InlineIo(),dispatch,connected)
    asyncio.run(engine.tick())
    assert ledger.inspect(owner,device,job['id'])['control_state']=='paused'
    connected_now=True;asyncio.run(engine.tick());assert not calls
    asyncio.run(engine.control(owner,device,job['id'],'resume'));asyncio.run(engine.tick());assert len(calls)==1


def test_moving_wait_scrolls_and_reobserves_without_completing_a_second_step(app,seller,monkeypatch):
    import oin_control_plane.seller as module
    owner,device,_,_,_=seller;ledger=app.state.seller_store;clock=[1000]
    monkeypatch.setattr(module,'now_seconds',lambda:clock[0])
    recipe=ledger.recipe(owner,device,{'name':'有效浏览','kind':'research','steps':[{'task_type':'observe','parameters':{},'expect':[START]},{'task_type':'wait','parameters':{'seconds':120,'browse_motion':True},'name':'浏览','effect':'read'}]})
    job=ledger.job(owner,device,{'recipe_id':recipe['id'],'idempotency_key':'moving-wait-once','due_at':1000})
    engine,calls=make_engine(app)
    for _ in range(3):asyncio.run(engine.tick())
    clock[0]=1020
    for _ in range(5):asyncio.run(engine.tick())
    scrolls=[task for task in calls if task['parameters'].get('action')=='scroll_page']
    assert len(scrolls)==1 and scrolls[0]['parameters']['direction']=='down'
    assert calls[-1]['task_type']=='observe'
    detail=ledger.inspect(owner,device,job['id'])
    assert detail['step_index']==1 and detail['steps'][1]['motion_count']==1
    asyncio.run(engine.control(owner,device,job['id'],'pause'))
    for _ in range(2):asyncio.run(engine.tick())
    count=len(calls);clock[0]=1300;asyncio.run(engine.tick());assert len(calls)==count
    assert ledger.inspect(owner,device,job['id'])['status']!='succeeded'


def test_wait_resume_cannot_inflate_remaining_from_stale_verification(app,seller,monkeypatch):
    import oin_control_plane.seller as module
    owner,device,_,_,_=seller;ledger=app.state.seller_store;clock=[1000]
    monkeypatch.setattr(module,'now_seconds',lambda:clock[0])
    recipe=ledger.recipe(owner,device,{'name':'恢复浏览计时','kind':'research','steps':[{'task_type':'observe','parameters':{},'expect':[START]},{'task_type':'wait','parameters':{'seconds':120,'browse_motion':True},'effect':'read'}]})
    job=ledger.job(owner,device,{'recipe_id':recipe['id'],'idempotency_key':'resume-stale-clock','due_at':1000})
    ledger.change(job['id'],status='running',step_index=1,control_state='paused',outcomes_json=json.dumps({'1':{'state':'current','started_at':1000,'duration_seconds':117,'last_verified_at':1003,'remaining_seconds':6489}}))
    clock[0]=7400
    engine,calls=make_engine(app)
    asyncio.run(engine.control(owner,device,job['id'],'resume'));asyncio.run(engine.tick())
    record=json.loads(ledger.read_job(job['id'])['outcomes_json'])['1']
    assert record['duration_seconds']==117
    assert record['last_verified_at']==7400
    assert module.wait_remaining(record)==117
    asyncio.run(engine.control(owner,device,job['id'],'pause'))
    record=json.loads(ledger.read_job(job['id'])['outcomes_json'])['1']
    assert record['remaining_seconds']<=117


def test_moving_wait_retries_slow_truncated_page_before_pausing(app,seller,monkeypatch):
    import oin_control_plane.seller as module
    owner,device,_,_,_=seller;ledger=app.state.seller_store;clock=[1000]
    monkeypatch.setattr(module,'now_seconds',lambda:clock[0])
    recipe=ledger.recipe(owner,device,{'name':'慢页面读取','kind':'research','steps':[{'task_type':'observe','parameters':{},'expect':[START]},{'task_type':'wait','parameters':{'seconds':120,'browse_motion':True},'effect':'read'}]})
    job=ledger.job(owner,device,{'recipe_id':recipe['id'],'idempotency_key':'slow-page-retry','due_at':1000})
    ledger.change(job['id'],status='running',step_index=1)
    calls=[]
    async def connected(device):return True
    async def dispatch(task):
        calls.append(task)
        app.state.store.complete_task(device,task['id'],'succeeded',{'package':'com.taobao.idlefish','truncated':len(calls)<3,'nodes':[]},None,None)
    engine=SellerEngine(ledger,InlineIo(),dispatch,connected)
    for _ in range(7):
        asyncio.run(engine.tick());clock[0]+=3
    fresh=ledger.read_job(job['id']);record=json.loads(fresh['outcomes_json'])['1']
    assert fresh['control_state']=='active'
    assert record['read_retries']==0
    assert record['duration_seconds']<=120
    assert len(calls)>=3
