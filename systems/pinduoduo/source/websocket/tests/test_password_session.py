"""账号密码会话离线契约：假浏览器与身份接口，不发平台请求。"""
import asyncio
import time
from types import SimpleNamespace
from login import password_session as module
from login.qr_state import QrOutcome, QrBrowserResult, PageProbe


def run(monkeypatch, *, login_cookie=True, human=False, bad=False, identity=None,
        cancel_identity=False, close_ok=True, form_error=False, expected=None):
    event = asyncio.Event()
    states, pages, verified, submitted = [], [], [], []
    class Page:
        def on(self, *_args): pass
        async def goto(self, *_args, **_kwargs): pass
        async def evaluate(self, *_args): return {'badPassword': bad, 'locked': False}
    page = Page()
    class Context:
        calls = 0
        async def new_page(self): return page
        async def cookies(self, *_args):
            self.calls += 1
            # 初始检查必须匿名；人工验证测试先返回一次未登录状态。
            threshold = 3 if human else 2
            return ([{'name': 'PASS_ID', 'value': 'fixture-pass'}]
                    if login_cookie and self.calls >= threshold else [])
    context = Context()
    class Native:
        closes = 0
        async def close(self): self.closes += 1
    class Driver:
        stopped = False
        async def stop(self): self.stopped = True
    native, driver = Native(), Driver()
    async def borrow():
        return SimpleNamespace(native=native, playwright=driver,
                               browser=SimpleNamespace(contexts=[context]))
    async def submit(_page, username, password, _timeout):
        submitted.append((username, password))
        if form_error: raise RuntimeError('fixture')
    async def classify(_page): return PageProbe(captcha_nodes=1 if human else 0)
    async def close(*_args): return close_ok
    async def sleep(_event, _seconds): await asyncio.sleep(.001)
    def identify(cookies):
        verified.append(cookies)
        if cancel_identity: event.set()
        return identity if identity is not None else {'shop_id': 'shop-a', 'user_id': 'user-a'}
    monkeypatch.setattr(module, 'borrow_warm_browser', borrow)
    monkeypatch.setattr(module, '_fill_and_submit', submit)
    monkeypatch.setattr(module, 'classify_page', classify)
    monkeypatch.setattr(module, '_close_browser', close)
    monkeypatch.setattr(module, '_sleep_or_cancel', sleep)
    monkeypatch.setattr(module, 'extract_login_identity', identify)
    monkeypatch.setattr(module, 'replenish_warm_browser', lambda: None)
    async def status(state, message): states.append((state, message))
    async def on_page(value): pages.append(value)
    async def scenario():
        return await module.run_password_session(username='fixture-user', password='fixture-secret',
            cancel_event=event, deadline_at=time.monotonic()+.08,
            on_state_change=status, on_page_ready=on_page, expected_shop_id=expected)
    result = asyncio.run(scenario())
    assert driver.stopped and native.closes and pages[-1] is None
    return SimpleNamespace(result=result, states=states, verified=verified, submitted=submitted)


def test_success_requires_new_cookie_and_identity_not_title(monkeypatch):
    h = run(monkeypatch)
    assert h.result.outcome == QrOutcome.CONFIRMED
    assert h.result.identity['shop_id'] == 'shop-a'
    assert h.submitted == [('fixture-user', 'fixture-secret')]


def test_challenge_is_recoverable_and_user_can_finish(monkeypatch):
    h = run(monkeypatch, human=True)
    assert QrOutcome.VERIFICATION_REQUIRED in [s for s, _ in h.states]
    assert h.result.outcome == QrOutcome.CONFIRMED


def test_form_change_opens_manual_fallback(monkeypatch):
    h = run(monkeypatch, form_error=True)
    assert QrOutcome.VERIFICATION_REQUIRED in [s for s, _ in h.states]
    assert h.result.outcome == QrOutcome.CONFIRMED


def test_platform_password_error_is_not_timeout(monkeypatch):
    h = run(monkeypatch, bad=True, login_cookie=False)
    assert h.result.outcome == QrOutcome.FAILED and '账号或密码错误' in h.result.message
    assert not h.verified and h.result.cookies is None


def test_no_cookie_never_means_success(monkeypatch):
    h = run(monkeypatch, login_cookie=False)
    assert h.result.outcome == QrOutcome.EXPIRED
    assert not h.verified and h.result.cookies is None


def test_cancel_during_identity_prevents_delivery(monkeypatch):
    h = run(monkeypatch, cancel_identity=True)
    assert h.result.outcome == QrOutcome.CANCELLED and h.result.cookies is None


def test_identity_failure_or_exit_failure_prevents_delivery(monkeypatch):
    for options in ({'identity': {}}, {'close_ok': False}):
        h = run(monkeypatch, **options)
        assert h.result.outcome == QrOutcome.FAILED and h.result.cookies is None


def test_wrong_shop_cannot_replace_target_credentials(monkeypatch):
    h = run(monkeypatch, expected='other-shop')
    assert h.result.outcome == QrOutcome.FAILED
    assert '不一致' in h.result.message and h.result.cookies is None


def test_auth_diagnostics_only_keeps_codes_and_filters_endpoint(caplog):
    import logging
    async def scenario():
        watcher = module.AuthDiagnostics()
        class Response:
            url = 'https://mms.pinduoduo.com/janus/api/auth'
            async def json(self):
                return {'success':False,'errorCode':2000020,'password':'never-log-fixture',
                        'result':{'token':'never-log-fixture','mobile':'never-log-fixture'}}
        response=Response()
        with caplog.at_level(logging.INFO):
            await watcher(response)
        assert watcher.error_code==2000020 and watcher.category=='phone_verification'
        assert 'never-log-fixture' not in caplog.text
        response.url='https://unrelated.example/janus/api/auth'
        watcher.error_code=None
        await watcher(response)
        assert watcher.error_code is None
    asyncio.run(scenario())
