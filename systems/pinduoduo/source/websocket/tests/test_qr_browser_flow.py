# -*- coding: utf-8 -*-
"""
websocket 扫码浏览器流程测试（本轮新增）
======================================
用**假 Playwright** 驱动 ``login.qr_browser.run_qr_login`` 的真实循环，覆盖上一轮
代码审查发现的缺陷。全程不启动浏览器、不触网、不接触任何真实账号。

重点回归用例：
- FIX-1：用户确认成功后页面会跳转、二维码元素随之消失。若循环先判"元素还在不在"，
  一次**成功扫码**会被误报成"二维码已失效"。现在成功判据（凭据 + 身份核验）先执行。
- FIX-2：``verification_required`` 是可恢复中间态，必须通过回调推给前端并**继续
  等待**，用户本人完成验证后仍可走到成功；到截止时间才以该状态收尾。
- FIX-3：身份核验必须节流，不能每秒打平台接口。
- FIX-4/5：用户取消后不得被后台结果"复活"，也不该再白开一个浏览器。
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from types import SimpleNamespace

import pytest

_WS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_REPO_ROOT = os.path.dirname(_WS_DIR)
for _path in (_REPO_ROOT, _WS_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from login import qr_browser, qr_session_store  # noqa: E402
from login.qr_state import QrOutcome  # noqa: E402


class _FakeElement:
    def __init__(self, present: bool):
        self._present = present

    async def bounding_box(self):
        return {"x": 0, "y": 0, "width": 120, "height": 120} if self._present else None

    async def get_attribute(self, _name):
        return "CANVAS"

    async def evaluate(self, _script):
        return "CANVAS"

    async def screenshot(self, *_a, **_kw):
        return b"\x89PNG-fake"


class _Script:
    """帧脚本：每轮迭代由 cookies() 推进一次，其余读取器看当前帧。"""

    def __init__(self, frames):
        self._frames = frames
        self.index = -1

    def advance(self):
        if self.index < len(self._frames) - 1:
            self.index += 1

    def current(self):
        return self._frames[max(0, self.index)]


class _FakeResponse:
    """取码接口响应替身（只提供 watcher 需要的 url 与 json）。"""

    def __init__(self, url, body):
        self.url = url
        self._body = body

    async def json(self):
        return self._body


class _FakePage:
    def __init__(self, script):
        self._script = script
        self.goto_calls = 0
        self.handlers = {}

    def on(self, event, handler):
        self.handlers.setdefault(event, []).append(handler)

    async def goto(self, *args, **kwargs):
        self.goto_calls += 1
        self._script.advance()

    async def wait_for_timeout(self, _ms):
        return None

    async def query_selector(self, selector):
        frame = self._script.current()
        if not frame.get("qr"):
            return None
        return _FakeElement(True)

    async def evaluate(self, script, arg=None):
        frame = self._script.current()
        probe = dict(frame.get("probe", {}))
        probe.setdefault("onLoginPage", True)
        probe.setdefault("leftLoginPage", False)
        return probe


class _FakeContext:
    def __init__(self, page, script):
        self._page = page
        self._script = script
        self.closed = False
        self.added_cookies = []
        self._fired_uri = None

    async def new_page(self):
        return self._page

    async def add_cookies(self, cookies):
        self.added_cookies.extend(cookies)

    async def cookies(self, _url=None):
        # 每轮迭代的第一次调用：在此推进帧，并把该帧的取码响应喂给真实 watcher。
        self._script.advance()
        frame = self._script.current()
        uri = frame.get("uri")
        if uri and self._fired_uri != uri:
            self._fired_uri = uri
            for handler in self._page.handlers.get("response", []):
                await handler(_FakeResponse(
                    "https://mms.pinduoduo.com/janus/api/scan/login/qrcode?t=1",
                    {"success": True, "result": {"uri": uri}}))
        return frame.get("cookies", [])

    async def close(self):
        self.closed = True


class _FakeBrowser:
    def __init__(self, page, script):
        self._page = page
        self._script = script
        self.contexts = []
        self.closed = False

    async def new_context(self, **kwargs):
        ctx = _FakeContext(self._page, self._script)
        self.contexts.append(ctx)
        return ctx

    async def close(self):
        self.closed = True


class _FakeChromium:
    executable_path = "/test-only/chrome"
    def __init__(self, page, script, counter):
        self._page = page
        self._script = script
        self._counter = counter
        self.browser = None

    async def launch(self, **kwargs):
        self._counter[0] += 1
        self.browser = _FakeBrowser(self._page, self._script)
        return self.browser


class _FakePlaywright:
    launch_count = 0

    def __init__(self, page, script, counter):
        self.chromium = _FakeChromium(page, script, counter)
        self.stopped = False

    async def start(self):
        return self

    async def stop(self):
        self.stopped = True

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _install_fake(monkeypatch, frames):
    """把 qr_browser 里的 async_playwright 换成假实现，返回可观察句柄。"""
    script = _Script(frames)
    page = _FakePage(script)
    counter = [0]
    holder = {}

    def factory():
        pw = _FakePlaywright(page, script, counter)
        holder["pw"] = pw
        return pw

    monkeypatch.setattr(qr_browser, "async_playwright", factory)
    return page, counter, holder


PASS_COOKIE = [{"name": "PASS_ID", "value": "fake", "domain": "mms.pinduoduo.com",
                "path": "/", "expires": -1, "httpOnly": True, "secure": True,
                "sameSite": "Lax"}]

# 免验证取码凭据（反爬令牌 + 匿名设备号）。真实值不进测试，这里只用假值。
CAPTCHA_COOKIE = [{"name": "msfe-pc-cookie-captcha-token", "value": "fake-captcha-token",
                   "domain": ".pinduoduo.com", "path": "/", "expires": -1,
                   "httpOnly": True, "secure": True, "sameSite": "None"},
                  {"name": "api_uid", "value": "fake-uid", "domain": ".pinduoduo.com",
                   "path": "/", "expires": -1, "httpOnly": False, "secure": False,
                   "sameSite": "Lax"}]

PNG_B64_PREFIX = "iVBORw0KGgo"


@pytest.fixture(autouse=True)
def _clean_verification_cache():
    """每个用例前后清空进程内免验证凭据，避免用例之间互相影响。"""
    qr_browser.reset_verification_cache_for_test()
    yield
    qr_browser.reset_verification_cache_for_test()


def _run(monkeypatch, frames, *, verify_result=None, wait=None, poll=None, deadline=2.0,
         verify_ok=True):
    """跑一次流程，返回 (result, states, rotations, verify_calls)。"""
    page, counter, holder = _install_fake(monkeypatch, frames)
    calls = {"n": 0}

    def fake_identity(_cookies):
        calls["n"] += 1
        if not verify_ok:
            return None
        return verify_result if verify_result is not None else {"shop_id": 999, "shop_name": "店"}

    monkeypatch.setattr(qr_browser, "extract_login_identity", fake_identity)

    states, rotations = [], []
    qr_pngs = []
    # 节流窗口默认 5 秒，测试里压到 0 以便观察多次核验；单独用例验证节流本身。
    original_interval = qr_browser._VERIFY_MIN_INTERVAL_SECONDS
    original_poll = qr_browser._POLL_INTERVAL_SECONDS
    if wait is not None:
        qr_browser._VERIFY_MIN_INTERVAL_SECONDS = wait
    if poll is not None:
        qr_browser._POLL_INTERVAL_SECONDS = poll

    async def scenario():
        async def on_state(state, message):
            states.append(state)

        async def on_qr(png, rotation):
            rotations.append(rotation)
            qr_pngs.append(png)

        return await qr_browser.run_qr_login(
            deadline_at=time.monotonic() + deadline,
            cancel_event=asyncio.Event(),
            on_qr_update=on_qr,
            on_state_change=on_state,
        )

    try:
        result = asyncio.run(scenario())
    finally:
        qr_browser._VERIFY_MIN_INTERVAL_SECONDS = original_interval
        qr_browser._POLL_INTERVAL_SECONDS = original_poll
    result.extra["verify_calls"] = calls["n"]
    result.extra["launches"] = counter[0]
    result.extra["states"] = states
    result.extra["rotations"] = rotations
    result.extra["qr_pngs"] = qr_pngs
    browser = getattr(holder.get("pw").chromium, "browser", None)
    result.extra["context"] = browser.contexts[-1] if browser and browser.contexts else None
    return result


def test_native_launcher_uses_fresh_default_context_without_cookie_injection(monkeypatch):
    instances = []
    class FakeNative:
        async def start(self, chromium, executable):
            self.closed = False
            instances.append(self)
            browser = _FakeBrowser(chromium._page, chromium._script)
            browser.contexts.append(_FakeContext(chromium._page, chromium._script))
            chromium.browser = browser
            return browser
        async def close(self):
            self.closed = True
    monkeypatch.setattr(qr_browser, 'NativeChrome', FakeNative)
    monkeypatch.setattr(qr_browser, 'get_settings', lambda: SimpleNamespace(
        qr_login_browser_launcher='native'))
    async def no_cookie_injection(context):
        pytest.fail('native flow must start without cached verification cookies')
    monkeypatch.setattr(qr_browser, '_inject_verification_cookies', no_cookie_injection)
    result = _run(monkeypatch, [{'qr': True}, {'cookies': PASS_COOKIE}])
    assert result.outcome == QrOutcome.CONFIRMED
    assert result.extra['launches'] == 0
    assert result.extra['context'].closed
    assert not result.extra['context'].added_cookies
    assert instances[0].closed


def test_native_start_failure_closes_launcher_without_playwright_fallback(monkeypatch):
    instances = []
    class BrokenNative:
        async def start(self, chromium, executable):
            self.closed = False
            instances.append(self)
            raise RuntimeError('test-only startup failure')
        async def close(self):
            self.closed = True
    monkeypatch.setattr(qr_browser, 'NativeChrome', BrokenNative)
    monkeypatch.setattr(qr_browser, 'get_settings', lambda: SimpleNamespace(
        qr_login_browser_launcher='native'))
    result = _run(monkeypatch, [])
    assert result.outcome == QrOutcome.FAILED
    assert result.extra['launches'] == 0
    assert instances[0].closed


# ----------------------------------------------------------------------
def test_success_after_page_navigates_away_is_not_reported_expired(monkeypatch):
    """FIX-1 回归：二维码消失但凭据+身份核验通过，必须是 confirmed。"""
    frames = [
        {"qr": True, "probe": {}},
        {"qr": False, "probe": {"onLoginPage": False, "leftLoginPage": True},
         "cookies": PASS_COOKIE},
    ]
    result = _run(monkeypatch, frames)
    assert result.outcome == QrOutcome.CONFIRMED, result.message
    assert result.identity and result.identity["shop_id"] == 999


def test_leaving_login_page_without_valid_identity_fails_not_expires(monkeypatch):
    """FIX-1 另一半：页面跳走了但身份核验不过，应给准确失败而不是"二维码已失效"。"""
    frames = [
        {"qr": True, "probe": {}},
        {"qr": False, "probe": {"onLoginPage": False, "leftLoginPage": True},
         "cookies": PASS_COOKIE},
    ]
    result = _run(monkeypatch, frames, verify_ok=False, poll=0.05, deadline=0.5)
    assert result.outcome == QrOutcome.FAILED, result.message
    assert "身份核验未通过" in result.message
    assert result.cookies is None


def test_verification_required_is_recoverable(monkeypatch):
    """FIX-2：出现滑块时推中间态给前端并继续等，用户完成后仍可成功。"""
    frames = [
        {"qr": False, "probe": {"captchaNodes": 3}},
        {"qr": False, "probe": {"captchaNodes": 3}},
        {"qr": True, "probe": {}},
        {"qr": True, "probe": {}, "cookies": PASS_COOKIE},
    ]
    # 默认轮询间隔 1 秒、截止时间 2 秒不足以走完 4 帧脚本，压短间隔以便断言完整链路。
    result = _run(monkeypatch, frames, poll=0.05, deadline=1.0)
    assert QrOutcome.VERIFICATION_REQUIRED in result.extra["states"]
    assert result.outcome == QrOutcome.CONFIRMED, result.message


def test_verification_required_persists_until_deadline(monkeypatch):
    """FIX-2：验证一直没过，最终以 verification_required 收尾，而不是笼统超时。"""
    frames = [{"qr": False, "probe": {"captchaNodes": 3}}]
    result = _run(monkeypatch, frames, poll=0.05, deadline=0.3)
    assert result.outcome == QrOutcome.VERIFICATION_REQUIRED
    assert "不会代为" in result.message or "安全验证" in result.message


def test_identity_verification_is_throttled(monkeypatch):
    """FIX-3：PASS_ID 出现后不得每秒都打平台核验接口。"""
    frames = [{"qr": True, "probe": {}, "cookies": PASS_COOKIE}] * 6
    # 节流窗口设得比整个 deadline 还长 → 整个流程只应核验 1 次。
    result = _run(monkeypatch, frames, wait=10.0, deadline=0.4)
    assert result.extra["verify_calls"] <= 1, result.extra["verify_calls"]


def test_no_qr_and_no_challenge_fails(monkeypatch):
    """既没出码也没验证拦路：结构变更，按失败给准确原因。"""
    frames = [{"qr": False, "probe": {}}]
    result = _run(monkeypatch, frames, deadline=0.3)
    assert result.outcome == QrOutcome.FAILED
    assert "页面结构" in result.message


def test_cancel_before_launch_does_not_start_browser(monkeypatch):
    """FIX-5：取消发生在启动浏览器之前，不应白开一个浏览器。"""
    frames = [{"qr": True, "probe": {}}]
    page, counter, _ = _install_fake(monkeypatch, frames)
    monkeypatch.setattr(qr_browser, "extract_login_identity", lambda _c: {"shop_id": 1})

    async def scenario():
        ev = asyncio.Event()
        ev.set()
        return await qr_browser.run_qr_login(
            deadline_at=time.monotonic() + 2.0, cancel_event=ev,
        )

    result = asyncio.run(scenario())
    assert result.outcome == QrOutcome.CANCELLED
    assert counter[0] == 0


def test_browser_resources_closed_on_every_terminal(monkeypatch):
    """终态必须关闭 context 与 browser，不残留浏览器进程。"""
    frames = [{"qr": False, "probe": {}}]
    page, counter, holder = _install_fake(monkeypatch, frames)
    monkeypatch.setattr(qr_browser, "extract_login_identity", lambda _c: {"shop_id": 1})

    async def scenario():
        return await qr_browser.run_qr_login(
            deadline_at=time.monotonic() + 0.3, cancel_event=asyncio.Event(),
        )

    result = asyncio.run(scenario())
    pw = holder.get("pw")
    assert result.outcome == QrOutcome.FAILED
    assert pw is not None and pw.stopped is True


# ----------------------------------------------------------------------
# FIX-4：注册表层面，取消的会话不得被后台结果复活
# ----------------------------------------------------------------------
def test_cancelled_session_is_not_resurrected_by_backend_result():
    qr_session_store.reset_for_test()
    session = qr_session_store.QrLoginSession(
        session_id="s-1", owner_user_id=7, target_shop_id=None,
        state=QrOutcome.CANCELLED,
    )
    qr_session_store._SESSIONS["s-1"] = session
    qr_session_store._apply_result(
        session,
        qr_browser.QrBrowserResult(outcome=QrOutcome.CONFIRMED, message="成功",
                                   cookies={"PASS_ID": "x"},
                                   identity={"shop_id": 999}),
    )
    assert session.state == QrOutcome.CANCELLED
    assert session._cookies is None
    assert session.shop_id is None


def test_display_state_cannot_overwrite_terminal():
    qr_session_store.reset_for_test()
    session = qr_session_store.QrLoginSession(
        session_id="s-2", owner_user_id=7, target_shop_id=None,
        state=QrOutcome.CONFIRMED,
    )
    qr_session_store._SESSIONS["s-2"] = session

    async def scenario():
        await qr_session_store.update_display_state("s-2", QrOutcome.WAITING, "倒退")

    asyncio.run(scenario())
    assert session.state == QrOutcome.CONFIRMED


def test_stuck_session_is_force_expired_on_next_sweep():
    """后台任务已死但没写回结果时，会话必须被强制落到明确终态，不能无限轮询。"""
    qr_session_store.reset_for_test()
    session = qr_session_store.QrLoginSession(
        session_id="s-3", owner_user_id=7, target_shop_id=None,
        state=QrOutcome.WAITING, qr_png_b64="QUJD",
    )

    async def _dead():
        return None

    task = asyncio.get_event_loop_policy().new_event_loop()
    session.task = _DoneTask()
    qr_session_store._SESSIONS["s-3"] = session

    async def scenario():
        async with qr_session_store._lock():
            qr_session_store._sweep_locked()
        return session.state

    state = asyncio.run(scenario())
    assert state == QrOutcome.EXPIRED
    assert session.qr_png_b64 is None


class _DoneTask:
    """一个"已完成"的任务替身。"""

    def done(self):
        return True

    def cancel(self):
        return None


def test_qr_image_is_encoded_by_us_from_the_official_response_uri(monkeypatch):
    """二维码必须由本轮接口 uri 自己编码，而不是截页面 canvas。"""
    uri = "https://w.url.cn/s/test?type=1&data=newTicket$fake-only"
    import base64 as _b64
    png = qr_browser._render_qr_png(uri)
    assert png and png.startswith(PNG_B64_PREFIX)
    assert _b64.b64decode(png).startswith(b"\x89PNG\r\n\x1a\n")


def test_official_uri_is_turned_into_our_own_qr(monkeypatch):
    """页面自己发出的取码响应里的 uri，会被我们编码成二维码推给前端。"""
    frames = [
        {"qr": True, "probe": {}, "uri": "https://w.url.cn/s/a?data=newTicket$round-1"},
    ]
    result = _run(monkeypatch, frames, poll=0.05, deadline=0.5)
    assert result.extra["qr_pngs"], "应至少推送一次二维码"
    assert result.extra["qr_pngs"][0].startswith(PNG_B64_PREFIX)
    assert result.extra["rotations"] == [1]


def test_qr_response_wakes_idle_loop_without_fixed_page_delay(monkeypatch):
    original_goto = _FakePage.goto
    async def goto_with_later_response(self, *args, **kwargs):
        await original_goto(self, *args, **kwargs)
        async def emit():
            await asyncio.sleep(.02)
            for handler in self.handlers.get('response', []):
                await handler(_FakeResponse(
                    'https://mms.pinduoduo.com/janus/api/scan/login/qrcode',
                    {'success': True, 'result': {
                        'uri': 'https://w.url.cn/s/test?data=newTicket$offline-fast'}}))
        asyncio.create_task(emit())
    async def no_fixed_delay(self, ms):
        pytest.fail('二维码处理不应依赖固定页面等待')
    monkeypatch.setattr(_FakePage, 'goto', goto_with_later_response)
    monkeypatch.setattr(_FakePage, 'wait_for_timeout', no_fixed_delay)
    # 常规页面检查间隔远大于会话期限；必须由响应事件唤醒才能发布二维码。
    result = _run(monkeypatch, [{'qr': True}], poll=5, deadline=.35)
    assert result.extra['rotations'] == [1]
    assert 'first_qr_published' in result.extra['timings_ms']


def test_qr_event_is_consumed_once_even_when_uri_is_not_published():
    async def scenario():
        watcher = qr_browser._QrResponseWatcher()
        watcher.changed.set()
        await watcher.wait_for_change(.2)
        assert not watcher.changed.is_set()
        task = asyncio.create_task(watcher.wait_for_change(.05))
        await asyncio.sleep(.01)
        assert not task.done(), '未处理的二维码不得导致等待器持续立即返回'
        await task
    asyncio.run(scenario())


def test_each_new_uri_rotates_our_qr(monkeypatch):
    """平台轮换二维码时，每一轮新 uri 都应生成新的二维码图。"""
    frames = [
        {"qr": True, "probe": {}},                     # 首帧被 goto 消耗
        {"qr": True, "probe": {}, "uri": "https://w.url.cn/s/a?data=newTicket$r1"},
        {"qr": True, "probe": {}, "uri": "https://w.url.cn/s/b?data=newTicket$r2"},
    ]
    result = _run(monkeypatch, frames, poll=0.05, deadline=0.6)
    assert result.extra["rotations"] == [1, 2]
    assert result.extra["qr_pngs"][0] != result.extra["qr_pngs"][1]


def test_render_qr_returns_none_on_unencodable_input():
    """uri 超出二维码容量时返回 None，不抛异常打断流程。"""
    assert qr_browser._render_qr_png("https://w.url.cn/s/x?data=" + "A" * 8000) is None


def test_verification_cookie_is_cached_then_injected_into_new_context(monkeypatch):
    """一次人工验证后拿到的免验证凭据要进内存缓存，并在下一次扫码时注入新上下文。"""
    frames = [{"qr": True, "probe": {}, "uri": "https://w.url.cn/s/a?data=newTicket$x",
               "cookies": CAPTCHA_COOKIE}]
    first = _run(monkeypatch, frames, deadline=0.3)
    assert qr_browser.has_verification_cookie() is True

    frames2 = [{"qr": True, "probe": {}, "uri": "https://w.url.cn/s/b?data=newTicket$y"}]
    second = _run(monkeypatch, frames2, deadline=0.3)
    injected = second.extra["context"].added_cookies
    assert {c["name"] for c in injected} == {"msfe-pc-cookie-captcha-token", "api_uid"}


def test_cached_verification_cookie_never_carries_login_state(monkeypatch):
    """缓存只收白名单反爬凭据：登录态 PASS_ID 绝不进缓存。"""
    kept = qr_browser.remember_verification_cookies(PASS_COOKIE + CAPTCHA_COOKIE)
    assert kept == 2
    assert "PASS_ID" not in qr_browser._CAPTCHA_COOKIES


def test_collects_verification_cookie_only_from_whitelist():
    """白名单之外的 Cookie（例如登录态）不应进入缓存。"""
    assert qr_browser.remember_verification_cookies(PASS_COOKIE) == 0
    assert qr_browser.has_verification_cookie() is False


def test_verification_cache_is_isolated_by_backend_owner():
    first = qr_browser.verification_owner.set(7)
    try:
        qr_browser.remember_verification_cookies(CAPTCHA_COOKIE)
        assert qr_browser.has_verification_cookie()
        second = qr_browser.verification_owner.set(8)
        try:
            assert not qr_browser.has_verification_cookie()
            assert qr_browser._verification_cache() == {}
        finally:
            qr_browser.verification_owner.reset(second)
        assert qr_browser.has_verification_cookie()
    finally:
        qr_browser.verification_owner.reset(first)


def test_watcher_rejects_foreign_origin_and_invalid_uri():
    async def scenario():
        watcher = qr_browser._QrResponseWatcher()
        valid = 'https://w.url.cn/s/test?data=newTicket$dummy-only'
        await watcher(_FakeResponse('https://other.example/janus/api/scan/login/qrcode',
                                   {'success':True,'result':{'uri':valid}}))
        assert watcher.uri is None
        await watcher(_FakeResponse('https://mms.pinduoduo.com/janus/api/scan/login/qrcode',
                                   {'success':True,'result':{'uri':'https://other.example/'}}))
        assert watcher.uri is None
        await watcher(_FakeResponse('https://mms.pinduoduo.com/janus/api/scan/login/qrcode',
                                   {'success':True,'result':{'uri':valid}}))
        assert watcher.uri == valid
    asyncio.run(scenario())


def test_transient_qr_absence_during_rotation_is_not_expiry(monkeypatch):
    """二维码轮换时元素短暂消失 1 个周期，不得判过期（2026-10-16 真实闭环踩过的坑）。"""
    # 首帧被 goto 消耗，故多放一帧 qr:True 作为轮询可见的初始画面。
    frames = [
        {"qr": True, "probe": {}},
        {"qr": True, "probe": {}},
        {"qr": False, "probe": {}},          # 轮换瞬间消失
        {"qr": True, "probe": {}},           # 立刻回来
        {"qr": True, "probe": {}, "cookies": PASS_COOKIE},
    ]
    result = _run(monkeypatch, frames, poll=0.05, deadline=1.5)
    assert result.outcome == QrOutcome.CONFIRMED, result.message


def test_persistent_qr_absence_still_expires(monkeypatch):
    """连续多次缺失仍要判过期，容错不能变成永不超时。"""
    monkeypatch.setattr(qr_browser, "_QR_ABSENT_SECONDS", 0.1)
    frames = [
        {"qr": True, "probe": {}},
        {"qr": True, "probe": {}},
        {"qr": False, "probe": {}},
        {"qr": False, "probe": {}},
        {"qr": False, "probe": {}},
    ]
    result = _run(monkeypatch, frames, poll=0.05, deadline=1.5)
    assert result.outcome == QrOutcome.EXPIRED, result.message
    assert result.message == "登录二维码已失效，请重新发起扫码"
