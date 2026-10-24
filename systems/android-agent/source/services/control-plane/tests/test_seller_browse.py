"""State-machine checks; the pages mirror observed Android 11 field placement."""
import asyncio
import json
import time
import pytest
from test_seller import seller, InlineIo
from oin_control_plane.seller import SellerEngine, validate_steps
from oin_control_plane.seller_browse import cards, detail, favorite_counter, PACKAGE, RID
from oin_control_plane.protocol import validate_task_request
from oin_control_plane.security import SecurityError


def node(text='',description='',rid='',bounds=None,**extra):
    return dict(text=text,description=description,resource_id=rid,bounds=bounds or [0,0,60,40],visible_to_user=True,enabled=True,**extra)


def page(nodes):return dict(package=PACKAGE,truncated=False,nodes=nodes)


def listing():
    nodes=[node('推荐'),node(description='我的，未选中状态')]
    for i in range(5):
        y=200+i*200
        nodes.extend([node('pic普通测试商品长标题'+str(i),bounds=[24,y,320,y+40]),node('¥',bounds=[24,y+60,45,y+85]),node('1',bounds=[45,y+60,60,y+85])])
    return page(nodes)


def product(i=0,count=5):
    return page([node(rid=RID+'back_appbar'),node(description='立即购买按钮',rid=RID+'detail_right_button'),
        node('普通测试商品长标题'+str(i)+'\n商品图文内容，包含尺寸、成色和说明。'),
        node(description='¥1，包邮',rid=RID+'detail_price_layout'),node('测试卖家',rid=RID+'user_info_nick'),
        node(description='收藏按钮',rid=RID+'detail_left_button',favorite_count=count)])


def test_current_text_titles_and_exact_unique_nodes():
    found=cards(listing());assert len(found)==5 and found[0]['selector']['kind']=='text'
    assert detail(product(),found[0])['seller']=='测试卖家'
    assert detail(product(1),found[0]) is None
    assert cards({**listing(),'truncated':True})==[]
    assert cards({**listing(),'package':'other'})==[]
    duplicate=listing();duplicate['nodes'].append(duplicate['nodes'][2]);assert len(cards(duplicate))==4
    assert favorite_counter(product())==5
    assert favorite_counter({**product(),'truncated':True}) is None


def setup(app,seller,monkeypatch,*,favorites=2,home_only=False,unchanged=False,fail_favorite=False,already_saved=False,fail_scroll=False,search_browse=False):
    monkeypatch.setattr('oin_control_plane.seller_browse.pick_card',lambda choices:choices[0] if choices else None)
    ledger=app.state.seller_store;owner,device,*_=seller
    with app.state.database.transaction(immediate=True) as c:c.execute('UPDATE devices SET app_version_code=58 WHERE id=?',(device,))
    params={'seconds':60,**({'favorites':favorites} if favorites else {}),**({'search_browse':True} if search_browse else {})}
    plan=[dict(task_type='observe',effect='read',parameters={},expect=[dict(kind='text',value='推荐')]),
          dict(task_type='content_browse',parameters=params,effect='commit' if favorites else 'navigation')]
    recipe=ledger.recipe(owner,device,dict(name='预热测试',kind='research',steps=plan))
    job=ledger.job(owner,device,dict(recipe_id=recipe['id'],authorize=True,idempotency_key='browse-integration-once'))
    calls=[];state={'item':None,'counts':{},'clock':int(time.time()),'search_stage':'home','keyword':''}
    monkeypatch.setattr('oin_control_plane.seller_browse.now_seconds',lambda:state['clock'])
    async def connected(device):return True
    async def dispatch(task):
        calls.append(task)
        if task['task_type']=='observe':
            result=listing() if state['item'] is None or home_only else product(state['item'],state['counts'].get(state['item'],5))
            if search_browse and state['item'] is None:
                if state['search_stage']=='home':result['nodes'].append(node(rid=RID+'search_bar_layout'))
                elif state['search_stage']=='editor':result=page([node(description='搜索'),node(state['keyword'],**{'class':'android.widget.EditText','editable':True})])
                else:
                    result=page([node(state['keyword'],rid=RID+'keyword_text'),node('价格')])
                    for i in range(5):
                        y=500+i*180
                        result['nodes'].extend([node(description='普通测试商品长标题'+str(i),bounds=[20,y,346,y+50]),node(description='1.00元',bounds=[20,y+60,94,y+100])])
        else:
            p=task['parameters'];selector=p.get('selector',{})
            if p['action']=='back':
                if state['item'] is not None:state['item']=None
                elif search_browse:state['search_stage']='home'
            elif p['action']=='set_node_text':state['keyword']=p['value']
            elif selector.get('value')==RID+'search_bar_layout':state['search_stage']='editor'
            elif selector.get('value')=='搜索':state['search_stage']='results'
            elif selector.get('value','').startswith('普通测试商品长标题'):state['item']=int(selector['value'][-1])
            elif selector.get('value','').startswith('pic普通'):state['item']=int(selector['value'][-1])
            elif selector.get('value')=='收藏按钮' and not unchanged and not fail_favorite:
                if already_saved and state['item']==0:
                    state['counts'][0]=4 if state['counts'].get(0,5)==5 else 5
                else:state['counts'][state['item']]=state['counts'].get(state['item'],5)+1
            result={'success':True}
        failed=fail_favorite and task['parameters'].get('selector',{}).get('value')=='收藏按钮' or fail_scroll and task['parameters'].get('action')=='scroll_page'
        app.state.store.complete_task(device,task['id'],'failed' if failed else 'succeeded',result,'lost_result' if failed else None,None)
    return ledger,job,SellerEngine(ledger,InlineIo(),dispatch,connected),calls,state


def ticks(ledger,job,engine,state,n=120):
    for _ in range(n):
        state['clock']+=4
        asyncio.run(engine.advance(ledger.read_job(job['id'])))


def test_reads_distinct_detail_pages_and_collects_exactly_two(app,seller,monkeypatch):
    ledger,job,engine,calls,state=setup(app,seller,monkeypatch)
    ticks(ledger,job,engine,state,180)
    saved=ledger.read_job(job['id']);assert saved['status']=='succeeded'
    result=json.loads(saved['outcomes_json'])['1']
    assert len(result['viewed_items'])>=3 and result['elapsed_seconds']>=60
    assert len(set(result['favorited_items']))==2
    assert sum(c['parameters'].get('selector',{}).get('value')=='收藏按钮' for c in calls)==2
    assert all(p['read_seconds']>=20 and p['scrolls']>=1 and p['content_confirmed'] for p in result['viewed_items'])
    assert not any('购买' in c['parameters'].get('selector',{}).get('value','') for c in calls)


def test_home_motion_cannot_finish_a_content_task(app,seller,monkeypatch):
    ledger,job,engine,calls,state=setup(app,seller,monkeypatch,home_only=True)
    ticks(ledger,job,engine,state,600)
    saved=ledger.read_job(job['id']);assert saved['control_state']=='paused' and saved['status']!='succeeded'
    assert saved['error_code'] in {'item_detail_not_confirmed','browse_no_readable_items'}
    assert not json.loads(saved['outcomes_json'])['1']['viewed_items']


@pytest.mark.parametrize('fail_favorite',[False,True])
def test_unknown_favorite_result_is_not_clicked_again_after_resume(app,seller,monkeypatch,fail_favorite):
    ledger,job,engine,calls,state=setup(app,seller,monkeypatch,unchanged=True,fail_favorite=fail_favorite)
    ticks(ledger,job,engine,state)
    assert ledger.read_job(job['id'])['control_state']=='paused'
    count=lambda:sum(c['parameters'].get('selector',{}).get('value')=='收藏按钮' for c in calls)
    assert count()==1
    asyncio.run(engine.control(seller[0],seller[1],job['id'],'resume'))
    ticks(ledger,job,engine,state)
    assert count()==1


def test_stop_and_pause_prevent_new_actions_and_preserve_current_item(app,seller,monkeypatch):
    ledger,job,engine,calls,state=setup(app,seller,monkeypatch,favorites=0)
    ticks(ledger,job,engine,state,9)
    asyncio.run(engine.control(seller[0],seller[1],job['id'],'pause'))
    ticks(ledger,job,engine,state,2)
    before=len(calls);ticks(ledger,job,engine,state);assert len(calls)==before
    asyncio.run(engine.control(seller[0],seller[1],job['id'],'stop'))
    ticks(ledger,job,engine,state);assert ledger.read_job(job['id'])['status']=='cancelled'
    assert len(calls)==before


def test_new_plan_and_search_editor_are_bounded():
    from oin_control_plane.seller_workflows import BUILTINS
    validate_steps(BUILTINS['daily_preheat_v5']['steps'],'research',template=True)
    params=dict(action='set_node_text',package=PACKAGE,selector=dict(kind='class',value='android.widget.EditText'),value='书签')
    assert validate_task_request('action',params,60)[1]==params
    for action in ['tap_unique_node','click_unique_node','click_node']:
        with pytest.raises(SecurityError):validate_task_request('action',{k:v for k,v in dict(params,action=action).items() if k!='value'},60)


def test_already_collected_item_is_restored_once_then_other_items_are_selected(app,seller,monkeypatch):
    ledger,job,engine,calls,state=setup(app,seller,monkeypatch,already_saved=True)
    ticks(ledger,job,engine,state,200)
    saved=ledger.read_job(job['id']);assert saved['status']=='succeeded'
    result=json.loads(saved['outcomes_json'])['1']
    assert state['counts'][0]==5 and len(result['favorited_items'])==2
    assert sum(c['parameters'].get('selector',{}).get('value')=='收藏按钮' for c in calls)==4


def test_measured_service_detail_has_separate_title_and_body():
    candidate={'title':'中英人工翻译润色 英语人工翻译 在线接单'}
    snapshot=page([node(rid=RID+'back_appbar'),node(rid=RID+'detail_right_button'),node('中英人工翻译润色',rid=RID+'skill_title_tv'),
        node('英语人工翻译 在线接单\n中英互译，翻译和文章润色保证用心处理。'),
        node(description='¥5，包邮',rid=RID+'detail_price_layout'),node('测试译者',rid=RID+'user_info_nick')])
    assert detail(snapshot,candidate)['description'].startswith('中英人工翻译润色\n英语人工翻译')
    assert detail(snapshot,{'title':'其他不对应的服务标题'}) is None


def test_persisted_consumed_observation_cannot_count_one_favorite_twice(app,seller,monkeypatch):
    ledger,job,engine,calls,state=setup(app,seller,monkeypatch)
    for _ in range(100):
        ticks(ledger,job,engine,state,1)
        saved=ledger.read_job(job['id']);record=json.loads(saved['outcomes_json']).get('1',{})
        if len(record.get('favorited_items',[]))==1:break
    assert len(record['favorited_items'])==1 and record['next_parameters']['action']=='back'
    # Reproduce process loss after outcome persistence, before clearing its old task pointer.
    ledger.change(job['id'],task_id=record['processed_task_id'])
    ticks(ledger,job,engine,state,1)
    replayed=json.loads(ledger.read_job(job['id'])['outcomes_json'])['1']
    assert replayed['favorited_items']==record['favorited_items']
    assert replayed['next_parameters']==record['next_parameters']


def test_resume_excludes_a_long_interruption_from_browse_time(app,seller,monkeypatch):
    ledger,job,engine,calls,state=setup(app,seller,monkeypatch,favorites=0)
    monkeypatch.setattr('oin_control_plane.seller.now_seconds',lambda:state['clock'])
    ticks(ledger,job,engine,state,9)
    asyncio.run(engine.control(seller[0],seller[1],job['id'],'pause'))
    ticks(ledger,job,engine,state,2)
    before=json.loads(ledger.read_job(job['id'])['outcomes_json'])['1']['active_seconds']
    state['clock']+=3600
    asyncio.run(engine.control(seller[0],seller[1],job['id'],'resume'))
    ticks(ledger,job,engine,state,2)
    after=json.loads(ledger.read_job(job['id'])['outcomes_json'])['1']['active_seconds']
    assert after==before


def test_recommendation_caption_uses_unique_same_card_seller_and_current_price():
    snapshot=page([node('推荐'),node(description='我的，未选中状态'),
        node('pic金属上色出众，接单从不踩雷',bounds=[31,726,337,805]),
        node('¥',bounds=[31,824,45,847]),node('89',bounds=[45,814,90,851]),node('.10',bounds=[90,819,127,849]),
        node('测试模型工作室',bounds=[74,863,195,895])])
    found=cards(snapshot);assert len(found)==1
    item=found[0];assert item['list_seller']=='测试模型工作室' and item['list_price_fen']==8910 and item['list_identity_unique']
    actual=page([node(rid=RID+'back_appbar'),node(rid=RID+'detail_right_button'),
        node('3D建模定制，超还原细节',rid=RID+'skill_title_tv'),node('工业级树脂打印服务，来图定制及人工金属上色。'),
        node(description='¥89.1，2人小刀价，包邮，直接买 ￥99.00',rid=RID+'detail_price_layout'),node('测试模型工作室',rid=RID+'user_info_nick')])
    assert detail(actual,item) is not None
    assert detail(actual,{**item,'list_seller':'其他卖家'}) is None
    assert detail(actual,{**item,'list_price_fen':8911}) is None
    assert detail(actual,{**item,'list_identity_unique':False}) is None


def test_explicit_resume_of_exhausted_purchase_keeps_finished_browse(app,seller,monkeypatch):
    ledger,job,engine,calls,state=setup(app,seller,monkeypatch,favorites=0)
    ledger.change(job['id'],step_index=1,control_state='paused',error_code='purchase_no_affordable_candidate')
    ledger.record_outcome(ledger.read_job(job['id']),{'state':'current','phase':'purchase_scan',
        'search_keyword':'书签','search_pages':8,'purchase_attempts':99,
        'active_seconds':610,'favorited_items':['first','second'],'viewed_items':[{'key':'first'}]})
    asyncio.run(engine.control(seller[0],seller[1],job['id'],'resume'))
    saved=ledger.read_job(job['id']);record=json.loads(saved['outcomes_json'])['1']
    assert saved['control_state']=='active' and saved['error_code'] is None
    assert record['phase']=='purchase_return_home' and record['search_keyword']!='书签'
    assert record['active_seconds']==610 and record['favorited_items']==['first','second']
    assert 'search_pages' not in record and calls==[]


def test_random_reading_pause_is_bounded_and_persisted(monkeypatch):
    from oin_control_plane.seller_browse import plan_reading
    monkeypatch.setattr('oin_control_plane.seller_browse.secrets.randbelow',lambda n:0)
    item={'read_seconds':20,'scrolls':0}
    plan_reading(item)
    assert item['reading_pause_seconds']==5 and item['next_read_action_at']==25
    assert item['next_scroll_direction']=='down'
    saved=dict(item)
    monkeypatch.setattr('oin_control_plane.seller_browse.secrets.randbelow',lambda n:n-1)
    plan_reading(item)
    assert item==saved  # no reroll during observe, pause/resume or restart
    other={'read_seconds':20,'scrolls':3,'scroll_depth':3}
    plan_reading(other)
    assert other['reading_pause_seconds']==10 and other['next_scroll_direction']=='up'
    assert other['next_read_action_at']==30


def test_long_details_have_multiple_read_scroll_segments_instead_of_a_long_idle_tail(app,seller,monkeypatch):
    import sys
    original=product
    def long_product(*args,**kwargs):
        snapshot=original(*args,**kwargs)
        snapshot['nodes'][2]['text']+='\n'+('尺寸、材质、成色和使用说明。'*80)
        return snapshot
    monkeypatch.setattr(sys.modules[__name__],'product',long_product)
    ledger,job,engine,calls,state=setup(app,seller,monkeypatch,favorites=0)
    last_call=0;movements=[0]
    for _ in range(120):
        ticks(ledger,job,engine,state,1)
        record=json.loads(ledger.read_job(job['id'])['outcomes_json']).get('1',{})
        item=record.get('current_item',{})
        for task in calls[last_call:]:
            if task['parameters'].get('action')=='scroll_page':movements.append(item['read_seconds'])
        last_call=len(calls)
        if record.get('viewed_items'):
            movements.append(record['viewed_items'][0]['read_seconds'])
            break
    assert len(movements)>=5  # at least three scrolls plus start/end
    assert max(b-a for a,b in zip(movements,movements[1:]))<=20
    assert all(c['parameters'].get('action') in {None,'tap_unique_node','scroll_page','back'} for c in calls)


def test_failed_scroll_does_not_count_as_actual_browsing_motion(app,seller,monkeypatch):
    ledger,job,engine,calls,state=setup(app,seller,monkeypatch,favorites=0,fail_scroll=True)
    ticks(ledger,job,engine,state,90)
    saved=ledger.read_job(job['id']);record=json.loads(saved['outcomes_json'])['1']
    assert saved['control_state']=='paused' and record['current_item']['scrolls']==0
    assert not record['viewed_items'] and 'pending_detail_scroll' not in record
    count=len(calls);ticks(ledger,job,engine,state,20);assert len(calls)==count


def test_everyday_search_enters_details_collects_two_and_changes_terms_without_purchasing(app,seller,monkeypatch):
    ledger,job,engine,calls,state=setup(app,seller,monkeypatch,search_browse=True)
    ticks(ledger,job,engine,state,260)
    saved=ledger.read_job(job['id']);assert saved['status']=='succeeded',saved
    record=json.loads(saved['outcomes_json'])['1']
    from oin_control_plane.browse_search import BROWSE_KEYWORDS
    keywords=[c['parameters']['value'] for c in calls if c['parameters'].get('action')=='set_node_text']
    assert len(keywords)>=2 and keywords[0]!=keywords[1] and set(keywords)<=set(BROWSE_KEYWORDS)
    assert len(record['viewed_items'])>=3 and len(record['favorited_items'])==2
    assert all(p['list_price_fen']==100 for p in record['viewed_items'])  # ordinary browsing accepts more than ten fen
    assert not any('购买' in c['parameters'].get('selector',{}).get('value','') for c in calls)


def test_search_browse_pause_preserves_keyword_plan_and_stops_dispatch(app,seller,monkeypatch):
    ledger,job,engine,calls,state=setup(app,seller,monkeypatch,search_browse=True)
    ticks(ledger,job,engine,state,12)
    asyncio.run(engine.control(seller[0],seller[1],job['id'],'pause'))
    ticks(ledger,job,engine,state,2)
    before=json.loads(ledger.read_job(job['id'])['outcomes_json'])['1'];count=len(calls)
    ticks(ledger,job,engine,state,15)
    assert len(calls)==count
    asyncio.run(engine.control(seller[0],seller[1],job['id'],'resume'))
    after=json.loads(ledger.read_job(job['id'])['outcomes_json'])['1']
    assert after['browse_keyword']==before['browse_keyword']


def test_both_public_tasks_enable_same_everyday_search_browse():
    from oin_control_plane.seller_workflows import BUILTINS
    from oin_control_plane.browse_search import BROWSE_KEYWORDS
    from oin_control_plane.purchase_handoff import DEFAULT_KEYWORDS
    assert len(BROWSE_KEYWORDS)==len(set(BROWSE_KEYWORDS))==50
    assert not set(BROWSE_KEYWORDS)&set(DEFAULT_KEYWORDS)
    for name,kind in [('daily_preheat_v5','research'),('daily_maintenance_v5','metrics')]:
        steps=validate_steps(BUILTINS[name]['steps'],kind,template=True)
        params=steps[-1]['parameters']
        assert params['search_browse'] is True and params['seconds']==600 and params['favorites']==2
        assert params['purchase_handoff'] is True
