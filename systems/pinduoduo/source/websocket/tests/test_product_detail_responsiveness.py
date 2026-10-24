"""详情失败有界，且同步平台查询不占用ASGI事件循环。"""
import asyncio
import threading
import time

import httpx
import pytest
from fastapi import FastAPI
from channel_pdd.api.get_product_detail import GetProductDetail
from channel_pdd.core import base_request
from routes import products


def test_detail_has_one_attempt_and_no_implicit_browser_login(monkeypatch):
    fetcher = GetProductDetail()
    attempts = []
    def timeout(*args, **kwargs):
        attempts.append(kwargs['timeout'])
        raise base_request.requests.exceptions.Timeout('test timeout')
    monkeypatch.setattr(base_request.requests, 'post', timeout)
    result = fetcher.fetch_detail('G1')
    assert not result['success']
    assert attempts == [(3, 5)]
    assert fetcher._relogin_and_update_cookies() is False


def test_slow_detail_does_not_block_event_loop(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    def slow(self, goods_id):
        entered.set()
        assert release.wait(3)
        return {'success': True, 'detail': {'goods_id': goods_id, 'specifications': []}}
    monkeypatch.setattr(GetProductDetail, 'fetch_detail', slow)
    app = FastAPI()
    app.include_router(products.router)
    @app.get('/heartbeat')
    async def heartbeat():
        return {'ok': True}
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            task = asyncio.create_task(client.post('/products/detail', json={'shop_id':'test', 'goods_id':'G1'}))
            # Constructor does not load real credentials during this isolated test.
            try:
                for _ in range(100):
                    if entered.is_set(): break
                    await asyncio.sleep(.01)
                assert entered.is_set()
                assert not task.done(), 'detail must still be waiting when heartbeat succeeds'
                response = await asyncio.wait_for(client.get('/heartbeat'), .5)
                assert response.json() == {'ok': True}
            finally:
                release.set()
                response = await task
            assert response.json()['success']
    monkeypatch.setattr(GetProductDetail, '__init__', lambda self, **kwargs: None)
    asyncio.run(run())
