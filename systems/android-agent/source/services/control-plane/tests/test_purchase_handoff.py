from oin_control_plane.purchase_handoff import choose_keyword,price_fen,candidates,detail_handoff


def test_retried_task_keeps_its_keyword():
    keyword=choose_keyword(choose=lambda words:words[0])
    assert choose_keyword(keyword,choose=lambda words:(_ for _ in ()).throw(AssertionError('must not choose again')))==keyword


def test_ten_fen_is_exact_and_advertised_partial_prices_are_rejected():
    assert price_fen('¥0.10')==10
    for value in ['0.1起','0.105','￥0.1 拼团','0.01+运费','1e-1',None]:assert price_fen(value) is None
    snapshot={'success':True,'truncated':False,'page':{'page':'HOME','rules_verified':True},'items':{'rules_verified':True,'cards':[
        {'title':'书签','seller':'卖家','price':'0.10','confidence':'sample_rule_complete'},
        {'title':'书签','seller':'卖家','price':'0.10','confidence':'sample_rule_complete'},
        {'title':'超预算','seller':'卖家','price':'0.11','confidence':'sample_rule_complete'}]}}
    found=candidates(snapshot,'书签');assert len(found)==1
    assert found[0]['total_state']=='not_confirmed' and found[0]['shipping_state']=='needs_human_check'
    assert candidates({**snapshot,'truncated':True},'书签')==[]


def test_handoff_requires_detail_identity_current_price_and_never_claims_order():
    candidate={'title':'书签','seller':'卖家','display_price_fen':10,'keyword':'书签','shipping_state':'needs_human_check'}
    def page(labels):return {'package':'com.taobao.idlefish','truncated':False,'nodes':[{'text':s,'visible_to_user':True} for s in labels]}
    ready=detail_handoff(page(['书签','¥0.10','我想要']),candidate)
    assert ready['state']=='ready_for_human' and '程序不下单' in ready['message']
    assert detail_handoff(page(['书签','¥0.11','我想要']),candidate) is None
    assert detail_handoff(page(['书签','¥0.10','推荐']),candidate) is None
    assert detail_handoff(page(['书签','书签','¥0.10','我想要']),candidate) is None
    assert detail_handoff(page(['书签','¥0.10','现金签到','我想要']),candidate) is None


def test_virtual_search_pool_is_fifty_unique_terms_and_legacy_tasks_keep_their_term():
    from oin_control_plane.purchase_handoff import DEFAULT_KEYWORDS,LEGACY_KEYWORDS
    assert len(DEFAULT_KEYWORDS)==len(set(DEFAULT_KEYWORDS))==50
    assert '情感咨询' in DEFAULT_KEYWORDS and '电子手账' in DEFAULT_KEYWORDS
    assert not set(DEFAULT_KEYWORDS)&set(LEGACY_KEYWORDS)
    for keyword in DEFAULT_KEYWORDS+LEGACY_KEYWORDS:
        assert choose_keyword(keyword)==keyword
    for keyword in DEFAULT_KEYWORDS:
        assert choose_keyword(choose=lambda words:keyword)==keyword
