from types import SimpleNamespace
from app.services import connection_notify


def test_saved_credentials_request_real_reconnect(monkeypatch):
    captured=[]
    def post(base,path,payload,**kw):
        captured.append(payload)
        return SimpleNamespace(success=True)
    monkeypatch.setattr(connection_notify.service_client,'post_json',post)
    assert connection_notify.notify_connect(2,'shop',7)
    assert captured==[{'shop_pk':2,'shop_id':'shop','owner_user_id':7,'force_reconnect':True}]
