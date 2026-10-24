import asyncio
import pytest

from test_seller import seller, InlineIo
from oin_control_plane.seller import SellerEngine
from oin_control_plane.security import SecurityError
from oin_control_plane.seller_page import brighten_status


def page(texts):
    return {'package':'com.taobao.idlefish','truncated':False,'nodes':[
        {'text':t,'visible_to_user':True,'enabled':True,'clickable':False} for t in texts]}


def my(banner='一键擦亮 曝光+5'):
    return page(['我发布的','我的',banner,'超强擦亮'])


def goods(titles, count):
    return {'package':'com.taobao.idlefish','truncated':False,'nodes':[
        {'description':t,'visible_to_user':True} for t in [f'{count}\n在卖','草稿','已下架',
            '今日数据\n0\n宝贝曝光\n0\n宝贝浏览\n试试超强擦亮',
            *[f'编辑\n{name}\n曝光{n}\n浏览1\n想要0\n¥\n1' for n,name in enumerate(titles)]]]}


def setup(app,seller,kind,snapshots,*,fail_click=False,fail_code='lost_result'):
    owner,device,_,_,_=seller;ledger=app.state.seller_store
    with app.state.database.transaction(immediate=True) as c:
        c.execute('UPDATE devices SET app_version_code=52 WHERE id=?',(device,))
    start={'kind':'text','value':'我的'} if kind=='brighten_all' else {'kind':'description','value':'草稿'}
    plan=[{'task_type':'observe','parameters':{},'effect':'read','expect':[start]},
          {'task_type':kind,'parameters':{},'effect':'commit' if kind=='brighten_all' else 'read'}]
    recipe=ledger.recipe(owner,device,{'name':'维护引擎测试','kind':'metrics','steps':plan})
    job=ledger.job(owner,device,{'recipe_id':recipe['id'],'authorize':kind=='brighten_all','idempotency_key':'maintenance-test-once'})
    calls=[];observed=[0]
    async def connected(device):return True
    async def dispatch(task):
        calls.append(task)
        click=task['parameters'].get('action') in {'tap_unique_node','click_unique_node'}
        if task['task_type']=='observe':
            result=snapshots[min(observed[0],len(snapshots)-1)];observed[0]+=1
        else:result={'success':True}
        app.state.store.complete_task(device,task['id'],'failed' if click and fail_click else 'succeeded',result,fail_code if click and fail_click else None,None)
    return ledger,job,SellerEngine(ledger,InlineIo(),dispatch,connected),calls,recipe


def ticks(engine,count=25):
    for _ in range(count):asyncio.run(engine.tick())


def test_free_bulk_brighten_once_then_confirm_and_finish(app,seller):
    ledger,job,engine,calls,_=setup(app,seller,'brighten_all',[my(),my(),my('明日擦亮 曝光+5')])
    ticks(engine)
    detail=ledger.inspect(seller[0],seller[1],job['id'])
    assert detail['status']=='succeeded'
    clicks=[c for c in calls if c['task_type']=='action']
    assert len(clicks)==1 and clicks[0]['parameters']['selector']['value']=='一键擦亮 曝光+5'
    assert detail['steps'][1]['confirmed'] is True
    assert not ledger.committed(ledger.read_job(job['id']))


def test_already_used_bulk_brighten_does_not_click(app,seller):
    ledger,job,engine,calls,_=setup(app,seller,'brighten_all',[my('明日擦亮 曝光+5')])
    ticks(engine)
    assert ledger.read_job(job['id'])['status']=='succeeded'
    assert all(c['task_type']=='observe' for c in calls)


def test_paid_promotion_never_replaces_free_brighten(app,seller):
    ledger,job,engine,calls,_=setup(app,seller,'brighten_all',[my('试试超强擦亮')])
    ticks(engine)
    assert ledger.read_job(job['id'])['control_state']=='paused'
    assert all(c['task_type']=='observe' for c in calls)
    duplicate=my();duplicate['nodes'].append(duplicate['nodes'][2])
    assert brighten_status(duplicate)['state']=='unavailable'
    assert brighten_status({**my(),'truncated':True})['state']=='wrong_page'


@pytest.mark.parametrize('fail_click',[True,False])
def test_uncertain_brighten_is_never_replayed(app,seller,fail_click):
    ledger,job,engine,calls,_=setup(app,seller,'brighten_all',[my()],fail_click=fail_click)
    ticks(engine)
    assert ledger.read_job(job['id'])['status']=='unknown'
    ticks(engine)
    assert sum(c['task_type']=='action' for c in calls)==1


def test_scan_merges_overlapping_pages_reads_every_item_and_resumes(app,seller):
    first=goods(['商品甲','商品乙'],3);second=goods(['商品乙','商品丙'],3)
    ledger,job,engine,calls,_=setup(app,seller,'scan_goods',[first,first,second])
    ticks(engine,4)
    asyncio.run(engine.control(seller[0],seller[1],job['id'],'pause'))
    prior=len(calls);ticks(engine);assert len(calls)==prior
    asyncio.run(engine.control(seller[0],seller[1],job['id'],'resume'))
    # A new engine resumes the persisted page/product state.
    engine=SellerEngine(ledger,InlineIo(),engine.dispatch,engine.connected)
    ticks(engine)
    assert ledger.read_job(job['id'])['status']=='succeeded'
    result=ledger.product_metrics(seller[0],seller[1])
    assert {p['title'] for p in result['products']}=={'商品甲','商品乙','商品丙'}
    scrolls=[c for c in calls if c['task_type']=='action']
    assert len(scrolls)==1 and scrolls[0]['parameters']['direction']=='down'
    assert all(p['source_task_id'] for p in result['products'])


def test_scan_does_not_claim_all_items_when_stuck_or_duplicate_titles(app,seller):
    first=goods(['同名商品'],2)
    ledger,job,engine,calls,_=setup(app,seller,'scan_goods',[first])
    ticks(engine)
    assert ledger.read_job(job['id'])['control_state']=='paused'
    assert ledger.read_job(job['id'])['error_code']=='seller_collection_incomplete'
    assert ledger.product_metrics(seller[0],seller[1])['state']=='not_collected'


def test_new_bulk_step_requires_explicit_authorization(app,seller):
    ledger,job,engine,calls,recipe=setup(app,seller,'brighten_all',[my()])
    with pytest.raises(SecurityError,match='explicit_action_authorization_required'):
        ledger.job(seller[0],seller[1],{'recipe_id':recipe['id'],'idempotency_key':'not-authorized-test'})


def test_paid_page_after_click_is_returned_without_payment_or_second_brighten(app,seller):
    ledger,job,engine,calls,_=setup(app,seller,'brighten_all',[
        my(),my(),page(['超强擦亮','立即支付']),my()])
    ticks(engine)
    assert [c['parameters']['action'] for c in calls if c['task_type']=='action']==['tap_unique_node','back']
    assert ledger.read_job(job['id'])['status']=='unknown'


def test_scan_closes_identifiable_ad_and_rechecks_before_collection(app,seller):
    ad=page(['广告','关闭广告'])
    ledger,job,engine,calls,_=setup(app,seller,'scan_goods',[
        goods(['商品甲'],1),ad,goods(['商品甲'],1)])
    ticks(engine)
    assert ledger.read_job(job['id'])['status']=='succeeded'
    actions=[c for c in calls if c['task_type']=='action']
    assert len(actions)==1 and actions[0]['parameters']['selector']['value']=='关闭广告'


def test_empty_list_is_collected_truthfully(app,seller):
    empty=goods([],0);empty['nodes'].append({'description':'你还没有在卖的宝贝，\n去发布','visible_to_user':True})
    ledger,job,engine,calls,_=setup(app,seller,'scan_goods',[empty])
    ticks(engine)
    assert ledger.read_job(job['id'])['status']=='succeeded'
    assert ledger.product_metrics(seller[0],seller[1])['state']=='empty_goods'
    assert all(t['task_type']=='observe' for t in calls)


def test_stop_before_bulk_click_does_not_dispatch_it(app,seller):
    ledger,job,engine,calls,_=setup(app,seller,'brighten_all',[my()])
    ticks(engine,4)  # observe -> store free selector, before any action insertion
    asyncio.run(engine.control(seller[0],seller[1],job['id'],'stop'))
    ticks(engine)
    assert ledger.read_job(job['id'])['status']=='cancelled'
    assert all(t['task_type']=='observe' for t in calls)


def test_screen_off_before_click_pauses_without_unknown_or_automatic_retry(app,seller):
    ledger,job,engine,calls,_=setup(app,seller,'brighten_all',[my()],fail_click=True,fail_code='screen_off')
    ticks(engine)
    assert ledger.read_job(job['id'])['control_state']=='paused'
    assert ledger.read_job(job['id'])['status']!='unknown'
    count=len(calls);ticks(engine);assert len(calls)==count


def test_current_maintenance_plan_uses_full_scan_and_bulk_step(app,seller):
    from oin_control_plane.seller_workflows import BUILTINS
    from oin_control_plane.seller import validate_steps
    plan=BUILTINS['daily_maintenance_v4']
    steps=validate_steps(plan['steps'],plan['kind'],template=True)
    types=[s['task_type'] for s in steps]
    assert types.count('scan_goods')==1 and types.count('brighten_all')==1
    assert 'manual' not in types
    assert types.index('scan_goods')<types.index('brighten_all')
    assert sum(s['parameters'].get('seconds',0) for s in steps if s['task_type']=='wait')==600


def test_measured_today_brighten_label_is_free_entry_and_paid_label_is_not():
    from oin_control_plane.seller_page import brighten_status
    snap=my();snap['nodes']=[n for n in snap['nodes'] if '一键擦亮' not in (n.get('text','')+n.get('description',''))]
    snap['nodes'].append({'text':'今日擦亮 曝光+5','visible_to_user':True,'enabled':True,'clickable':False})
    found=brighten_status(snap)
    assert found['state']=='entry' and found['parameters']['selector']=={'kind':'text','value':'今日擦亮 曝光+5'}
    snap['nodes'][-1]['text']='超强擦亮'
    # The known selling-list entry remains safe; the paid label is never selected.
    fallback=brighten_status(snap)
    assert fallback['state']=='entry' and fallback['parameters']['selector']['value']=='我发布的'


def test_measured_two_layer_free_brighten_and_post_click_paid_offer(app,seller):
    entry=my('今日擦亮 曝光+5')
    before=page(['1\n在卖','草稿','已下架','今日数据\n0\n宝贝曝光\n0\n宝贝浏览\n加曝光\n一键擦亮\n+5曝光'])
    after=page(['1\n在卖','草稿','已下架','今日数据\n0\n宝贝曝光\n0\n宝贝浏览\n加曝光\n试试超强擦亮\n+500曝光'])
    assert brighten_status(before)['parameters']['action']=='tap_free_brighten'
    assert brighten_status(after)['state']=='already_done'
    ledger,job,engine,calls,_=setup(app,seller,'brighten_all',[entry,entry,before,after])
    ticks(engine,40)
    assert ledger.read_job(job['id'])['status']=='succeeded'
    actions=[c['parameters'] for c in calls if c['task_type']=='action']
    assert len(actions)==2 and actions[0]['selector']['value']=='今日擦亮 曝光+5'
    assert actions[1]=={'action':'tap_free_brighten','package':'com.taobao.idlefish'}


def test_unknown_task_can_be_ended_without_claiming_prior_action_succeeded(app,seller):
    import json
    ledger,job,engine,calls,_=setup(app,seller,'brighten_all',[my()])
    ticks(engine)
    assert ledger.read_job(job['id'])['status']=='unknown'
    stopped=ledger.reconcile(seller[0],seller[1],job['id'],{'outcome':'cancel'})
    assert stopped['status']=='cancelled'
    record=json.loads(ledger.read_job(job['id'])['outcomes_json'])
    assert record['_control_events'][-1]['effect_outcome']=='unknown'
    before=len(calls);ticks(engine);assert len(calls)==before


def test_post_brighten_personal_banner_is_not_required_to_resume_maintenance(app,seller):
    snapshot=my('发闲置 曝光+30')
    assert brighten_status(snapshot)=={'state':'entry','parameters':{'action':'tap_unique_node','package':'com.taobao.idlefish','selector':{'kind':'text','value':'我发布的'}}}
    after=page(['1\n在卖','草稿','已下架','今日数据\n0\n宝贝曝光\n0\n宝贝浏览\n加曝光\n试试超强擦亮\n首单惊喜'])
    ledger,job,engine,calls,_=setup(app,seller,'brighten_all',[snapshot,snapshot,after])
    ticks(engine)
    assert ledger.read_job(job['id'])['status']=='succeeded'
    actions=[c['parameters'] for c in calls if c['task_type']=='action']
    assert len(actions)==1 and actions[0]['selector']['value']=='我发布的'
