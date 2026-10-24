from channel_pdd.api.get_chat_history import GetChatHistory
from channel_pdd.api.get_conversations import GetConversations

def history(monkeypatch,pages):
    obj=object.__new__(GetChatHistory);obj.shop_id='test';obj.user_id=1
    iterator=iter(pages)
    monkeypatch.setattr(obj,'fetch_page',lambda *a,**kw:next(iterator))
    monkeypatch.setattr(obj,'_parse_message',lambda raw:{**raw,'ts':int(raw['msg_id'])})
    monkeypatch.setattr('channel_pdd.api.get_chat_history.time.sleep',lambda _:None)
    return obj

def test_history_partial_failure_retains_cursor(monkeypatch):
    obj=history(monkeypatch,[{'success':True,'messages':[{'msg_id':'2'}],'has_more':True},{'success':False,'error_msg':'授权失效'}])
    result=obj.fetch_all('c')
    assert result['success'] and not result['complete'] and result['next_cursor']=='2' and result['sync_error']=='授权失效'

def test_history_one_page_resume_and_no_progress(monkeypatch):
    obj=history(monkeypatch,[{'success':True,'messages':[{'msg_id':'2'}],'has_more':True}])
    result=obj.fetch_all('c',cursor='3',max_pages=1)
    assert not result['complete'] and result['next_cursor']=='2'
    obj=history(monkeypatch,[{'success':True,'messages':[{'msg_id':'2'}],'has_more':True}])
    result=obj.fetch_all('c',cursor='2',max_pages=1)
    assert result['sync_error'] and not result['complete']

def test_storage_failure_cannot_skip_unsaved_page(monkeypatch):
    obj=history(monkeypatch,[])
    monkeypatch.setattr(obj,'fetch_all',lambda *a,**kw:{'success':True,'messages':[{'msg_id':'2'}],'complete':False,'next_cursor':'2'})
    monkeypatch.setattr(obj,'_persist_history',lambda *a:(_ for _ in ()).throw(RuntimeError('test db down')))
    result=obj.fetch_all_and_persist(1,'c',cursor='3',max_pages=1)
    assert result['storage_error'] and result['next_cursor']=='3' and not result['complete']

def test_conversation_partial_error_is_not_complete(monkeypatch):
    obj=object.__new__(GetConversations);obj.shop_id='test'
    pages=iter([{'success':True,'conversations':[{'customer_uid':'c'}],'has_more':True},{'success':False,'error_msg':'限流'}])
    monkeypatch.setattr(obj,'fetch',lambda **kw:next(pages))
    result=obj.fetch_all()
    assert result['conversations'] and not result['complete'] and result['next_cursor']==2 and result['sync_error']=='限流'
