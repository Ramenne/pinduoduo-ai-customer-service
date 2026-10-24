import asyncio
import json
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from common.models.shop_models import Account, Shop
from common.utils.crypto import encrypt_text, try_decrypt_text
from channel_pdd.core.credential_store import credential_snapshot, save_session_observation
from channel_pdd import session_health as health


@pytest.fixture
def account_db(delivery_db):
    with delivery_db.begin() as s:
        s.add(Account(id=1, shop_pk=1, user_id=9, username='fixture',
            cookies_enc=encrypt_text(json.dumps({'PASS_ID':'original'})), login_state='relogin'))
    health._cache.clear()
    return delivery_db


def read_account(db):
    with db() as s:
        a=s.get(Account,1)
        return a.login_state,json.loads(try_decrypt_text(a.cookies_enc))


def test_late_refresh_and_expiry_cannot_replace_new_authorization(account_db):
    old=credential_snapshot('shop_1',9)
    with account_db.begin() as s:
        s.get(Account,1).cookies_enc=encrypt_text(json.dumps({'PASS_ID':'new-authorization'}))
    assert not save_session_observation(old,'relogin')
    assert not save_session_observation(old,'online',{'PASS_ID':'late-refresh'})
    assert read_account(account_db)[1]=={'PASS_ID':'new-authorization'}


@pytest.mark.parametrize('change',['disabled','owner_changed','merged'])
def test_observation_fences_account_scope(account_db,change):
    snapshot=credential_snapshot('shop_1',9)
    with account_db.begin() as s:
        sh=s.get(Shop,1)
        if change=='disabled':sh.status=0
        elif change=='owner_changed':sh.owner_user_id=10
        else:sh.merged_into_shop_pk=2
    assert not save_session_observation(snapshot,'online',{'PASS_ID':'wrong'})
    assert read_account(account_db)[0]=='relogin'


def test_renew_seeds_authoritative_cookies_without_old_profile(account_db,monkeypatch):
    def probe(snapshot,cookies):
        return health.SessionResult('online',cookies=cookies)
    seen=[]
    def browser(cookies):
        seen.append(dict(cookies))
        return {'state':'candidate','cookies':{'PASS_ID':'renewed'}}
    monkeypatch.setattr(health,'probe_identity',probe)
    monkeypatch.setattr(health,'_refresh_browser',browser)
    result=health.maintain_session('shop_1',9)
    assert result.state=='online'
    assert seen==[{'PASS_ID':'original'}]
    assert read_account(account_db)==('online',{'PASS_ID':'renewed'})
    result=health.maintain_session('shop_1',9)
    assert result.reason=='cooldown' and len(seen)==1


def test_browser_failure_preserves_identity_proven_session(account_db,monkeypatch):
    monkeypatch.setattr(health,'probe_identity',lambda s,c:health.SessionResult('online',cookies=c))
    monkeypatch.setattr(health,'_refresh_browser',lambda c:{'state':'temporary_error'})
    result=health.maintain_session('shop_1',9)
    assert result.state=='online' and result.reason=='renewal_deferred'
    assert read_account(account_db)==('online',{'PASS_ID':'original'})


def test_network_failure_does_not_mark_relogin_or_replace_cookies(account_db,monkeypatch):
    with account_db.begin() as s:s.get(Account,1).login_state='online'
    monkeypatch.setattr(health,'probe_identity',lambda s,c:health.SessionResult('temporary_error','network'))
    monkeypatch.setattr(health,'_refresh_browser',lambda c:pytest.fail('network failure must not launch login'))
    assert health.maintain_session('shop_1',9).state=='temporary_error'
    assert read_account(account_db)==('online',{'PASS_ID':'original'})


def test_expired_session_enters_human_cooldown_and_new_authorization_wakes_it(account_db,monkeypatch):
    monkeypatch.setattr(health,'probe_identity',lambda s,c:health.SessionResult('expired'))
    browser=[]
    monkeypatch.setattr(health,'_refresh_browser',lambda c:browser.append(c) or {'state':'expired'})
    assert health.maintain_session('shop_1',9).state=='expired'
    assert health.maintain_session('shop_1',9).reason=='cooldown'
    assert len(browser)==1
    with account_db.begin() as s:s.get(Account,1).cookies_enc=encrypt_text(json.dumps({'PASS_ID':'new'}))
    monkeypatch.setattr(health,'probe_identity',lambda s,c:health.SessionResult('online',cookies=c))
    assert health.maintain_session('shop_1',9,renew=False).state=='online'


def test_new_authorization_during_browser_discards_late_result(account_db,monkeypatch):
    monkeypatch.setattr(health,'probe_identity',lambda s,c:health.SessionResult('online',cookies=c))
    def browser(c):
        with account_db.begin() as s:s.get(Account,1).cookies_enc=encrypt_text(json.dumps({'PASS_ID':'new'}))
        return {'state':'candidate','cookies':{'PASS_ID':'late'}}
    monkeypatch.setattr(health,'_refresh_browser',browser)
    assert health.maintain_session('shop_1',9).state=='stale'
    assert read_account(account_db)[1]=={'PASS_ID':'new'}


def test_cancelled_refresh_cannot_write_back(account_db,monkeypatch):
    cancel=threading.Event()
    monkeypatch.setattr(health,'probe_identity',lambda s,c:health.SessionResult('online',cookies=c))
    def browser(c):
        cancel.set()
        return {'state':'candidate','cookies':{'PASS_ID':'late'}}
    monkeypatch.setattr(health,'_refresh_browser',browser)
    assert health.maintain_session('shop_1',9,cancelled=cancel).state=='stale'
    assert read_account(account_db)==('relogin',{'PASS_ID':'original'})


def test_same_account_concurrent_refresh_is_single_flight(account_db,monkeypatch):
    entered=threading.Event();release=threading.Event();out=[]
    def probe(s,c):
        entered.set();release.wait(2)
        return health.SessionResult('online',cookies=c)
    monkeypatch.setattr(health,'probe_identity',probe)
    thread=threading.Thread(target=lambda:out.append(health.maintain_session('shop_1',9,renew=False)))
    thread.start();assert entered.wait(2)
    assert health.maintain_session('shop_1',9).state=='busy'
    release.set();thread.join(3)
    assert out[0].state=='online'


def test_foreign_mall_or_forbidden_is_not_auth_expiry(monkeypatch):
    import requests
    class Client:
        def __enter__(self):return self
        def __exit__(self,*a):pass
        cookies=SimpleNamespace(get_dict=lambda:{})
        def post(self,*a,**k):return SimpleNamespace(status_code=200,json=lambda:{'success':True,'result':{'mall_id':'foreign'}})
    monkeypatch.setattr(requests,'Session',Client)
    snapshot=SimpleNamespace(shop_id='expected')
    assert health.probe_identity(snapshot,{}).reason=='identity_mismatch'
    monkeypatch.setattr(Client,'post',lambda *a,**k:SimpleNamespace(status_code=403))
    assert health.probe_identity(snapshot,{}).state=='temporary_error'


def test_auth_wait_resumes_without_manual_connect(monkeypatch):
    from channel_pdd.pdd_channel import PDDChannel
    from channel_pdd.core.pdd_token import AuthenticationRequired
    c=PDDChannel('fixture',9,token_provider=lambda *a:'token')
    results=iter([health.SessionResult('expired'),health.SessionResult('online')])
    c.recover_session=lambda **k:next(results)
    async def sleep_or_stop(delay):return False
    c._sleep_or_stop=sleep_or_stop
    calls=[]
    async def connect():
        calls.append(1)
        if len(calls)==1:raise AuthenticationRequired()
        c._stop_event.set()
    c._connect_once=connect
    async def run():
        c._stop_event=asyncio.Event()
        await asyncio.wait_for(c._connect_with_retry(),2)
    asyncio.run(run())
    assert len(calls)==2


def test_healthy_connection_clears_persisted_label(account_db,monkeypatch):
    import channel_pdd.pdd_channel as mod
    from channel_pdd.core.pdd_config import HeartbeatConfig
    class Socket:
        async def close(self):pass
    class Context:
        async def __aenter__(self):return Socket()
        async def __aexit__(self,*a):pass
    monkeypatch.setattr(mod.websockets,'connect',lambda *a,**k:Context())
    c=mod.PDDChannel('shop_1',9,token_provider=lambda *a:'token',heartbeat_config=HeartbeatConfig(enable_heartbeat=False))
    c.observe_credentials=lambda:credential_snapshot('shop_1',9)
    c.save_login_observation=save_session_observation
    c._set_cs_online=AsyncMock();c._message_loop=AsyncMock()
    async def run():
        c._stop_event=asyncio.Event()
        await c._connect_once()
    asyncio.run(run())
    assert read_account(account_db)[0]=='online'


@pytest.mark.parametrize('payload',[[],None,{'success':True,'result':['unexpected']}])
def test_invalid_identity_payload_is_temporary(monkeypatch,payload):
    import requests
    class Client:
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def post(self,*a,**k):
            assert k['allow_redirects'] is False
            return SimpleNamespace(status_code=200,json=lambda:payload)
    monkeypatch.setattr(requests,'Session',Client)
    assert health.probe_identity(SimpleNamespace(shop_id='expected'),{}).state=='temporary_error'


@pytest.mark.parametrize('url,state',[
    ('https://mms.pinduoduo.com/home/','candidate'),
    ('https://mms.pinduoduo.com/login/','expired'),
    ('https://outside.invalid/','temporary_error'),
])
def test_browser_refresh_uses_current_cookies_and_closes(monkeypatch,url,state):
    from login import session_refresh
    context=SimpleNamespace(add_cookies=AsyncMock(),cookies=AsyncMock(return_value=[{'name':'PASS_ID','value':'renewed'}]))
    page=SimpleNamespace(url=url,goto=AsyncMock(return_value=SimpleNamespace(status=200)),wait_for_timeout=AsyncMock(),
        get_by_text=lambda *a,**k:SimpleNamespace(count=AsyncMock(return_value=0)))
    context.new_page=AsyncMock(return_value=page)
    browser=SimpleNamespace(new_context=AsyncMock(return_value=context),close=AsyncMock())
    driver=SimpleNamespace(chromium=SimpleNamespace(launch=AsyncMock(return_value=browser)))
    class Playwright:
        async def __aenter__(self):return driver
        async def __aexit__(self,*a):pass
    monkeypatch.setattr(session_refresh,'async_playwright',Playwright)
    result=asyncio.run(session_refresh.refresh_from_cookies({'PASS_ID':'current'}))
    assert result['state']==state
    assert context.add_cookies.await_args.args[0][0]['value']=='current'
    browser.close.assert_awaited_once()
