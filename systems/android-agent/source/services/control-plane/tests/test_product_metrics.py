from test_seller import seller
from datetime import datetime
from zoneinfo import ZoneInfo
from oin_control_plane.product_metrics import extract,trends


def page(value='今日曝光 100'):
    return {'package':'com.taobao.idlefish','truncated':False,'nodes':[{'description':v,'visible_to_user':True} for v in ['在卖','草稿','已下架','实际商品名称\n'+value+'\n浏览 30\n想要 2']]}


def at(day,hour=22):return int(datetime(2026,10,day,hour,tzinfo=ZoneInfo('Asia/Shanghai')).timestamp())


def test_only_confirmed_seller_semantics_produce_product_metrics():
    value=extract(page(),at(14));assert value['state']=='captured'
    assert value['products'][0]['title']=='实际商品名称'
    assert value['products'][0]['exposure']==100
    assert extract({**page(),'truncated':True},at(14))['products']==[]
    assert extract({**page(),'package':'other'},at(14))['products']==[]
    assert extract(page('今日曝光 1.2万'),at(14))['products']==[]
    duplicate=page();duplicate['nodes'].append(duplicate['nodes'][-1]);assert extract(duplicate,at(14))['products']==[]


def test_exposure_decline_requires_same_product_scope_and_time():
    current=extract(page('今日曝光 60'),at(14));old=extract(page('今日曝光 100'),at(13))
    value=trends([old,current])[0];assert value['trend']=='down' and value['change']==-40
    assert '关键词' in value['advice']
    assert trends([extract(page('今日曝光 20'),at(14,8)),old])[0]['trend']=='baseline_needed'
    assert trends([extract(page('曝光 10'),at(14)),extract(page('曝光 100'),at(13))])[0]['trend']=='scope_unknown'
    assert trends([extract(page('累计曝光 10'),at(14)),extract(page('累计曝光 100'),at(13))])[0]['trend']=='baseline_needed'
    assert trends([current])[0]['trend']=='baseline_needed'


def populated_phone_page():
    # Captured task 3a40b456-92ad-478a-8c57-fbc214d553a5, Xianyu 7.25.40.
    texts=['1\n在卖','草稿','已下架',
        '今日数据\n0\n宝贝曝光\n0\n宝贝浏览\n加曝光\n今日已擦亮',
        '完成职业认证，获得更多曝光\n编辑\n情感咨询\n曝光0\n   \n浏览1\n   \n想要0\n¥\n1']
    return {'package':'com.taobao.idlefish','truncated':False,
        'nodes':[{'description':v,'visible_to_user':True} for v in texts]}


def test_actual_flutter_card_keeps_item_title_separate_from_account_aggregate():
    from oin_control_plane.seller_page import already_brightened
    from oin_control_plane.seller import matches
    phone=populated_phone_page()
    result=extract(phone,at(14))
    assert result['state']=='captured'
    assert [(v['title'],v['exposure'],v['浏览'],v['想要']) for v in result['products']]==[('情感咨询',0,1,0)]
    assert result['products'][0]['scope']=='unknown'
    assert already_brightened(phone)
    assert matches(phone,[{'kind':'description','value':v} for v in ['在卖','草稿','已下架']])
    for changed in [{**phone,'truncated':True},{**phone,'package':'other'}]:
        assert not already_brightened(changed)
        assert not extract(changed,at(14))['products']
    phone['nodes'][3]['description']='今日数据\n0\n宝贝曝光\n试试超强擦亮'
    assert not already_brightened(phone)


def test_duplicate_real_cards_or_missing_seller_context_are_not_accepted():
    phone=populated_phone_page()
    phone['nodes'].append(phone['nodes'][-1])
    assert extract(phone,at(14))['products']==[]
    phone=populated_phone_page();phone['nodes'][0]['description']='在卖推荐'
    assert extract(phone,at(14))['state']=='unconfirmed'


def test_metrics_read_is_device_scoped_and_empty_snapshot_hides_history(app,seller):
    import json
    from oin_control_plane.security import canonical_json
    owner,device,_,_,recipe=seller;ledger=app.state.seller_store
    job=ledger.job(owner,device,{'recipe_id':recipe,'authorize':True,'idempotency_key':'metrics-store-once'})
    metrics=extract(page(),at(14))
    with app.state.database.transaction(immediate=True) as c:
        c.execute("UPDATE seller_jobs SET kind='metrics',outcomes_json=? WHERE id=?",(canonical_json({'0':{'product_metrics':metrics}}),job['id']))
    assert ledger.product_metrics(owner,device)['products'][0]['title']=='实际商品名称'
    empty={'state':'empty_goods','captured_at':at(15),'products':[]}
    with app.state.database.transaction(immediate=True) as c:c.execute("UPDATE seller_jobs SET outcomes_json=? WHERE id=?",(json.dumps({'0':{'product_metrics':metrics},'1':{'product_metrics':empty}}),job['id']))
    assert ledger.product_metrics(owner,device)['products']==[]
