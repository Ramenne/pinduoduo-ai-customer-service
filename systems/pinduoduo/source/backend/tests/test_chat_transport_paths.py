import logging
from common.utils.access_log import AccessTokenFilter
from common.services import service_client


def test_backend_callbacks_follow_mounted_api_prefix(monkeypatch):
    monkeypatch.setenv('BACKEND_WEB_API_PREFIX','/api')
    assert service_client.backend_api_path('/internal/chat-events')=='/api/internal/chat-events'
    monkeypatch.setenv('BACKEND_WEB_API_PREFIX','/api/v1/')
    assert service_client.backend_api_path('/products/sync')=='/api/v1/products/sync'


def test_websocket_query_token_is_not_logged():
    record=logging.LogRecord('uvicorn.access',20,'',0,'%s %s',('GET','/api/chat/ws/1?token=fixture-sensitive&x=1'),None)
    AccessTokenFilter().filter(record)
    assert 'fixture-sensitive' not in record.getMessage()
    assert 'token=[redacted]&x=1' in record.getMessage()
