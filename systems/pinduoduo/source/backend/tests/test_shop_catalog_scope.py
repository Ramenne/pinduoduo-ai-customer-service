from contextlib import contextmanager
from common.models.knowledge_models import Product
from app.services import chat_workspace_service
from .test_chat_workspace import env
from .test_chat_api import chat_env


def test_catalog_respects_shop_and_enabled_state(db_session,env,monkeypatch):
    from agent import product_catalog
    data,user=env;pk=data['shop_a_pk']
    for id,shop,name,status in [(991,pk,'蜜薯',1),(992,pk,'已停用商品',0),(993,data['shop_b_pk'],'其他店商品',1)]:
        db_session.add(Product(id=id,shop_pk=shop,goods_id=str(id),goods_name=name,status=status,price=38.9))
    db_session.flush()
    @contextmanager
    def scope():yield db_session
    monkeypatch.setattr(product_catalog,'session_scope',scope)
    text=product_catalog.list_shop_products({'shop_id':pk})
    assert '蜜薯' in text and '其他店商品' not in text and '已停用商品' not in text
    assert '不是实时库存' in text
    assert '蜜薯' in product_catalog.product_details(pk,'991')
    assert '未找到' in product_catalog.product_details(pk,'993')
    shops=chat_workspace_service.list_shops(db_session,user).data['list']
    assert shops[0]['shop_products'][0]['goods_name']=='蜜薯'


def test_history_resync_enriches_placeholder_without_duplicates(db_session,env,monkeypatch):
    from common.models.log_models import ChatMessage
    from channel_pdd.api import get_chat_history as history
    data,user=env;pk=data['shop_a_pk']
    row=ChatMessage(shop_pk=pk,customer_uid='C1',msg_id='card-fixture',direction='in',msg_type='system_status',content='不支持的消息类型: 31')
    db_session.add(row);db_session.flush();original_id=row.id
    monkeypatch.setattr(history,'run_in_session',lambda fn:fn(db_session))
    fetcher=object.__new__(history.GetChatHistory);fetcher.user_id=user.id
    count=fetcher._persist_history(pk,'C1',[{'msg_id':'card-fixture','msg_type':'goods_inquiry','content':'[商品咨询] 蜜薯','goods_context':{'goods_id':'991','goods_name':'蜜薯'}}])
    assert count==0 and row.id==original_id and row.msg_type=='goods_inquiry'
    assert '991' in row.goods_context and '蜜薯' in row.content
