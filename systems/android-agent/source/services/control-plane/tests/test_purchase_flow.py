from oin_control_plane.purchase_flow import advance,search_candidates,search_results,RID
from test_seller_browse import node,page,listing,product
from oin_control_plane.seller_browse import home,detail


def results():
    return page([node('书签',rid=RID+'keyword_text'),node('价格'),
        node(description='普通纸质书签十张小包',bounds=[20,500,346,550]),node(description='0.10元',bounds=[20,560,94,600]),
        node(description='互拍互评小店经验商品',bounds=[374,500,700,550]),node(description='0.01元',bounds=[374,560,448,600])])


def run(record,snapshot):return advance(record,snapshot,snapshot['nodes'],home=home,detail=detail)


def test_search_keeps_keyword_and_chooses_exact_visible_affordable_item():
    snap=results();assert search_results(snap['nodes'],'书签')
    found=search_candidates(snap['nodes']);assert len(found)==1 and found[0]['list_price_fen']==10
    record={'phase':'purchase_scan','search_keyword':'书签'}
    parameters,after,error=run(record,snap)
    assert after=='purchase_detail' and not error
    assert parameters['selector']['value']=='普通纸质书签十张小包'
    assert record['search_keyword']=='书签'


def test_unknown_price_and_no_candidate_are_not_business_success():
    snap=results();snap['nodes'][3]['description']='0.10元起'
    record={'phase':'purchase_scan','search_keyword':'书签','search_pages':7}
    assert run(record,snap)==(None,None,'purchase_no_affordable_candidate')
    assert 'purchase_handoff' not in record


def test_checkout_terminal_is_read_only_and_does_not_tap_order_button():
    record={'phase':'purchase_checkout','search_keyword':'书签','purchase_detail_confirmed':True,'purchase_item':{'title':'普通纸质书签十张小包'}}
    params,after,error=run(record,page([node(description='关闭页面'),node(description='普通纸质书签十张小包'),node(description='运费\n包邮'),node(description='支付宝支付，已选择，按钮, 支付宝支付'),node(description='确认购买 0.10，按钮, 确认购买\n¥0.10')]))
    assert params is None and after=='purchase_ready' and not error
    assert record['purchase_handoff']['total_state']=='confirmed'
    assert run({'phase':'purchase_checkout','search_keyword':'书签'},page([node('立即支付')]))[2]=='purchase_checkout_not_confirmed'


def test_search_editor_requires_one_editable_nonpassword_field():
    record={'phase':'purchase_input','search_keyword':'书签'}
    snap=page([node(description='搜索'),node(**{'class':'android.widget.EditText','editable':True})])
    params,after,error=run(record,snap)
    assert params['action']=='set_node_text' and params['value']=='书签' and after=='purchase_typed'
    snap['nodes'].append(snap['nodes'][1]);assert run(record,snap)[0] is None


def test_retry_resets_only_search_stage_and_uses_a_different_keyword():
    from oin_control_plane.purchase_flow import retry_search
    record={'phase':'purchase_scan','search_keyword':'书签','search_pages':8,'purchase_attempts':100,
        'purchase_item':{'title':'旧候选'},'purchase_detail_confirmed':True,
        'viewed_items':[{'key':'one'}],'favorited_items':['one','two'],'active_seconds':605}
    retry_search(record)
    assert record['phase']=='purchase_return_home' and record['search_keyword']!='书签'
    assert record['viewed_items']==[{'key':'one'}] and record['favorited_items']==['one','two']
    assert record['active_seconds']==605 and 'search_pages' not in record and 'purchase_item' not in record
    assert run(record,page([node('未知页面')]))==({'action':'back'},'purchase_return_home',None)
    home_page=listing();home_page['nodes'].append(node(rid=RID+'search_bar_layout'))
    params,after,error=run(record,home_page)
    assert after=='purchase_input' and params['selector']['value']==RID+'search_bar_layout' and error is None


def test_free_brighten_action_has_no_caller_coordinates_and_exact_package():
    import pytest
    from oin_control_plane.protocol import validate_task_request
    from oin_control_plane.security import SecurityError
    p={'action':'tap_free_brighten','package':'com.taobao.idlefish'}
    assert validate_task_request('action',p,60)[1]==p
    for invalid in ({**p,'x':137},{**p,'package':'other'}):
        with pytest.raises(SecurityError):validate_task_request('action',invalid,60)


def test_passwordless_checkout_is_read_only_and_shipping_over_budget_is_skipped():
    snap=page([node(description='关闭页面'),node(description='普通纸质书签十张小包'),node(description='运费\n¥5.60'),
        node(description='支付宝支付，已选择，按钮, 支付宝支付'),node(description='免密支付 5.70，按钮, 免密支付\n¥5.70')])
    record={'phase':'purchase_checkout','search_keyword':'书签','purchase_detail_confirmed':True,'purchase_item':{'key':'old','title':'普通纸质书签十张小包'}}
    params,after,error=run(record,snap)
    assert params['selector']['value']=='关闭页面' and after=='purchase_return_results' and error is None
    assert 'purchase_handoff' not in record and record['purchase_rejected'][0]['total_fen']==570
    record['phase']=after
    assert run(record,page([node('商品详情')]))==({'action':'back'},'purchase_return_results',None)
    params,after,error=run(record,results());assert after=='purchase_detail' and error is None
    # A measured eligible total may be handed off, but the payment button is never selected.
    snap['nodes'][-1]['description']='免密支付 0.10，按钮, 免密支付\n¥0.10'
    snap['nodes'][2]['description']='运费\n包邮'
    record={'phase':'purchase_checkout','search_keyword':'书签','purchase_detail_confirmed':True,'purchase_item':{'title':'普通纸质书签十张小包'}}
    params,after,error=run(record,snap)
    assert params is None and after=='purchase_ready' and record['purchase_handoff']['total_fen']==10


def test_over_budget_sheet_can_be_closed_even_if_checkout_title_is_shortened():
    snap=page([node(description='关闭页面'),node(description='闲置门挂式收纳袋'),node(description='运费\n¥5.00'),
        node(description='支付宝支付，已选择，按钮, 支付宝支付'),node(description='免密支付 5.10，按钮, 免密支付\n¥5.10')])
    record={'phase':'purchase_checkout','search_keyword':'收纳袋','purchase_detail_confirmed':True,
        'purchase_item':{'title':'随单免费送 闲置门挂式收纳袋 黄色款'}}
    params,after,error=run(record,snap)
    assert after=='purchase_return_results' and params['selector']['value']=='关闭页面'
    snap['nodes'][-1]['description']='免密支付 0.10，按钮, 免密支付\n¥0.10'
    record={'phase':'purchase_checkout','search_keyword':'收纳袋','purchase_detail_confirmed':True,
        'purchase_item':{'title':'随单免费送 闲置门挂式收纳袋 黄色款'}}
    assert run(record,snap)==(None,None,None)  # never hand off an unidentified eligible item


def test_virtual_checkout_without_freight_uses_final_total_and_stays_on_sheet():
    for amount,fen in [('0.01',1),('0.10',10)]:
        record={'phase':'purchase_checkout','search_keyword':'电子手账','purchase_detail_confirmed':True,
                'purchase_item':{'title':'原创电子手账模板'}}
        snap=page([node(description='关闭页面'),node(description='原创电子手账模板'),
            node(description='支付宝支付，已选择，按钮, 支付宝支付'),
            node(description=f'免密支付 {amount}，按钮, 免密支付\n¥{amount}')])
        params,after,error=run(record,snap)
        assert params is None and after=='purchase_ready' and error is None
        assert record['purchase_handoff']['total_fen']==fen


def test_no_freight_is_not_proof_of_eligible_total():
    def record():return {'phase':'purchase_checkout','search_keyword':'电子手账','purchase_detail_confirmed':True,
                         'purchase_item':{'title':'原创电子手账模板'}}
    snap=page([node(description='关闭页面'),node(description='原创电子手账模板'),
        node(description='支付宝支付，已选择，按钮, 支付宝支付')])
    assert run(record(),snap)==(None,None,None)
    snap['nodes'].append(node(description='免密支付 0.10，按钮, 免密支付\n¥1.00'))
    assert run(record(),snap)==(None,None,None)
    snap['nodes'][-1]['description']='免密支付 1.00，按钮, 免密支付\n¥1.00'
    params,after,error=run(record(),snap)
    assert params['selector']['value']=='关闭页面' and after=='purchase_return_results' and error is None
