# -*- coding: utf-8 -*-
"""混合扫码流程契约测试：假浏览器 + MockTransport，不启动浏览器、不触网。

覆盖用户认可原型后点名的接入前置项：
- 浏览器确认关闭后再发布二维码，避免两条链路竞争确认响应；
- 浏览器退出未确认时不得开始 HTTP 交接；
- 取消 / 超时可区分，任何失败都不交付 Cookie；
- 确认响应的 mallId、身份接口的 shop_id 与目标店铺三道归属校验；
- status=5、限登、传输抖动与持续断网各自可辨。
"""
from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace

import httpx

from login import qr_hybrid
from login.qr_state import QrOutcome

URI = "https://w.url.cn/s/test?type=1&data=newTicket$test-only-ticket"
UA = "test-only-agent"
PNG_B64_PREFIX = "iVBORw0KGgo"

MINT_COOKIES = [
    {"name": "_nano_fp", "value": "test-only-nano", "domain": ".pinduoduo.com", "path": "/"},
    {"name": "api_uid", "value": "test-only-uid", "domain": ".pinduoduo.com", "path": "/"},
]


# ----------------------------------------------------------------------
# 假浏览器
# ----------------------------------------------------------------------
class _FakeResponse:
    def __init__(self, url, body):
        self.url = url
        self._body = body

    async def json(self):
        return self._body


class _FakePage:
    def __init__(self, *, uri=URI, probe=None, goto_error=None, on_goto=None):
        self.uri = uri
        self.probe = dict(probe or {})
        self.goto_error = goto_error
        self.on_goto = on_goto
        self.handlers = []
        self.goto_calls = 0

    def on(self, event, handler):
        if event == "response":
            self.handlers.append(handler)

    async def goto(self, *_args, **_kwargs):
        self.goto_calls += 1
        if self.goto_error is not None:
            raise self.goto_error
        if self.on_goto is not None:
            self.on_goto()
        if self.uri:
            body = {"success": True, "result": {"uri": self.uri}}
            for handler in self.handlers:
                await handler(_FakeResponse(
                    "https://mms.pinduoduo.com/janus/api/scan/login/qrcode?t=1", body))

    async def evaluate(self, script, _arg=None):
        if "navigator.userAgent" in script:
            return UA
        if "captchaNodes" in script:
            return {**self.probe, "onLoginPage": True, "leftLoginPage": False}
        return {}


class _FakeContext:
    def __init__(self, page, cookies=None):
        self.page = page
        self.cookies_data = list(cookies if cookies is not None else MINT_COOKIES)
        self.closed = False

    async def new_page(self):
        return self.page

    async def cookies(self, _url=None):
        return [dict(c) for c in self.cookies_data]

    async def close(self):
        self.closed = True


class _FakeBrowser:
    def __init__(self, context):
        self.context = context
        self.contexts = [context]
        self.closed = False

    async def new_context(self, **_kwargs):
        return self.context

    async def close(self):
        # 与 Playwright 一致：关闭 browser 会一并关闭其 context。
        self.context.closed = True
        self.closed = True


class _FakeChromium:
    executable_path = "/test-only/chromium"

    def __init__(self, browser):
        self._browser = browser
        self.launch_kwargs = []

    async def launch(self, **kwargs):
        self.launch_kwargs.append(kwargs)
        return self._browser


class _FakePlaywright:
    def __init__(self, chromium):
        self.chromium = chromium
        self.stopped = False

    async def start(self):
        return self

    async def stop(self):
        self.stopped = True


class _FakeNative:
    """NativeChrome 替身；close/confirm_exited 的返回值可分别控制。"""

    def __init__(self, context, *, close_ok=True, exit_ok=True):
        self.context = context
        self.close_ok = close_ok
        self.exit_ok = exit_ok
        self.close_calls = 0
        self.confirm_calls = 0

    async def start(self, _chromium, _executable):
        return _FakeBrowser(self.context)

    async def close(self):
        self.close_calls += 1
        return self.close_ok

    async def confirm_exited(self, timeout=2.0):
        self.confirm_calls += 1
        return self.exit_ok


# ----------------------------------------------------------------------
# 假 HTTP（只在本机内存里响应）
# ----------------------------------------------------------------------
def _http(statuses, *, errors=0, set_cookie=True, status5_message=None,
          login_restricted=False, rotate_cookie=False, on_call=None):
    calls = {"n": 0, "requests": []}

    def handler(request):
        calls["n"] += 1
        calls["requests"].append(request)
        if on_call is not None:
            on_call(request, calls["n"])
        if calls["n"] <= errors:
            raise httpx.ConnectError("test-only network error", request=request)
        index = min(calls["n"] - errors - 1, len(statuses) - 1)
        status = statuses[index]
        result = {"status": status}
        headers = []
        if rotate_cookie and status != 3:
            headers.append(("set-cookie",
                            "api_uid=rotated-test-only; Path=/; Domain=mms.pinduoduo.com"))
        if status == 3:
            result["userInfoVO"] = {"id": 456, "mallId": 123}
            result["loginLimitStatus"] = 2 if login_restricted else None
            if set_cookie:
                headers.append(("set-cookie",
                                "PASS_ID=test-only-pass; Path=/; Secure; HttpOnly"))
        if status == 5 and status5_message is not None:
            result["message"] = status5_message
        return httpx.Response(200, json={"success": True, "result": result},
                              headers=headers)

    return httpx.MockTransport(handler), calls


# ----------------------------------------------------------------------
# 运行一次混合流程
# ----------------------------------------------------------------------
def _run(
    monkeypatch,
    *,
    launcher="playwright",
    statuses=(3,),
    errors=0,
    set_cookie=True,
    status5_message=None,
    login_restricted=False,
    rotate_cookie=False,
    identity=None,
    expected=None,
    deadline=3.0,
    uri=URI,
    probe=None,
    goto_error=None,
    native=None,
    on_qr_observe=None,
    cancel_hook=None,
    http_on_call=None,
    cancel_event=None,
    warm=False,
):
    cancel = cancel_event if cancel_event is not None else asyncio.Event()

    def goto_hook():
        if cancel_hook is not None:
            cancel_hook(cancel)

    page = _FakePage(uri=uri, probe=probe, goto_error=goto_error, on_goto=goto_hook)
    context = _FakeContext(page)
    browser = _FakeBrowser(context)
    playwright = _FakePlaywright(_FakeChromium(browser))
    monkeypatch.setattr(qr_hybrid, "async_playwright", lambda: playwright)
    monkeypatch.setattr(qr_hybrid, "_launch_options", lambda: {})
    monkeypatch.setattr(qr_hybrid, "get_settings", lambda: SimpleNamespace(
        qr_login_url="https://mms.pinduoduo.com/login/?login_tab=qrcode",
        qr_login_browser_launcher=launcher))

    async def _noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr(qr_hybrid, "_inject_verification_cookies", _noop)
    monkeypatch.setattr(qr_hybrid, "_refresh_verification_cache", _noop)
    monkeypatch.setattr(qr_hybrid, "_POLL_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr(qr_hybrid, "_MINT_POLL_SECONDS", 0.01)
    if warm:
        native = _FakeNative(context)
        async def borrow():
            return SimpleNamespace(playwright=playwright, native=native, browser=browser)
        monkeypatch.setattr(qr_hybrid, "borrow_warm_browser", borrow)
        def forbidden_start(*_args, **_kwargs):
            raise AssertionError("warm path must not start a new driver")
        monkeypatch.setattr(qr_hybrid, "async_playwright", forbidden_start)
    if native is not None:
        monkeypatch.setattr(qr_hybrid, "NativeChrome", lambda: native)

    verified = []

    def _identity(cookies):
        verified.append(dict(cookies))
        if callable(identity):
            return identity(cookies)
        if identity is not None:
            return identity
        return {"shop_id": 123, "user_id": 456, "shop_name": "测试店铺"}

    monkeypatch.setattr(qr_hybrid, "extract_login_identity", _identity)
    transport, http_calls = _http(
        statuses, errors=errors, set_cookie=set_cookie,
        status5_message=status5_message, login_restricted=login_restricted,
        rotate_cookie=rotate_cookie, on_call=http_on_call)

    states, qrs = [], []

    async def scenario():
        async def on_state(state, message):
            states.append((state, message))

        async def on_qr(png_b64, rotation):
            if on_qr_observe is not None:
                on_qr_observe(context, browser, native)
            qrs.append((png_b64, rotation))

        return await qr_hybrid.run_qr_hybrid(
            deadline_at=time.monotonic() + deadline,
            cancel_event=cancel,
            expected_shop_id=expected,
            on_qr_update=on_qr,
            on_state_change=on_state,
            http_transport=transport,
        )

    result = asyncio.run(scenario())
    return SimpleNamespace(result=result, page=page, context=context, browser=browser,
                           playwright=playwright, native=native, http=http_calls,
                           states=states, qrs=qrs, verified=verified, cancel=cancel)


# ----------------------------------------------------------------------
# 顺序、退出确认与资源清理
# ----------------------------------------------------------------------
def test_qr_is_published_after_browser_is_closed(monkeypatch):
    """首张二维码在浏览器退出后交给前端，确认响应只进入 HTTP。"""
    seen = {}

    def observe(context, browser, native):
        seen["context_closed"] = context.closed
        seen["browser_closed"] = browser.closed
        seen["native_calls"] = native.close_calls if native is not None else 0

    harness = _run(monkeypatch, on_qr_observe=observe)
    assert harness.result.outcome == QrOutcome.CONFIRMED
    assert seen == {"context_closed": True, "browser_closed": True, "native_calls": 0}
    timings = harness.result.extra["timings_ms"]
    assert timings["browser_exit_confirmed"] <= timings["first_qr_published"]


def test_unconfirmed_browser_exit_aborts_http_handoff(monkeypatch):
    """native.close() 成功但退出确认失败时，不得向平台发轮询请求。"""
    native = _FakeNative(_FakeContext(_FakePage()), exit_ok=False)
    harness = _run(monkeypatch, launcher="native", native=native)
    assert harness.result.outcome == QrOutcome.FAILED
    assert "未能确认退出" in harness.result.message
    assert harness.http["n"] == 0
    assert native.confirm_calls == 1


def test_native_close_failure_aborts_http_handoff(monkeypatch):
    native = _FakeNative(_FakeContext(_FakePage()), close_ok=False)
    harness = _run(monkeypatch, launcher="native", native=native)
    assert harness.result.outcome == QrOutcome.FAILED
    assert "未能确认退出" in harness.result.message
    assert harness.http["n"] == 0
    assert native.confirm_calls == 0


def test_unknown_launcher_fails_without_http(monkeypatch):
    harness = _run(monkeypatch, launcher="unsupported")
    assert harness.result.outcome == QrOutcome.FAILED
    assert harness.http["n"] == 0


def test_mint_exception_closes_browser_and_stops_driver(monkeypatch):
    harness = _run(monkeypatch, goto_error=RuntimeError("test-only goto failure"))
    assert harness.result.outcome == QrOutcome.FAILED
    assert harness.result.cookies is None
    assert harness.browser.closed is True
    assert harness.playwright.stopped is True
    assert harness.http["n"] == 0


# ----------------------------------------------------------------------
# 取消与超时
# ----------------------------------------------------------------------
def test_cancel_during_mint_does_not_start_http(monkeypatch):
    harness = _run(monkeypatch, cancel_hook=lambda event: event.set())
    assert harness.result.outcome == QrOutcome.CANCELLED
    assert harness.http["n"] == 0
    assert harness.context.closed is True


def test_cancel_during_http_poll_stops_without_delivery(monkeypatch):
    cancel = asyncio.Event()

    def cancel_on_first(_request, count):
        if count == 1:
            cancel.set()

    harness = _run(monkeypatch, statuses=(1,), http_on_call=cancel_on_first,
                   deadline=2.0, cancel_event=cancel)
    assert harness.result.outcome == QrOutcome.CANCELLED
    assert harness.result.cookies is None
    assert harness.http["n"] == 1


def test_deadline_expiry_after_mint(monkeypatch):
    harness = _run(monkeypatch, statuses=(1,), deadline=0.4)
    assert harness.result.outcome == QrOutcome.EXPIRED
    assert harness.result.cookies is None


def test_no_qr_and_no_challenge_fails_closed(monkeypatch):
    harness = _run(monkeypatch, uri="", deadline=0.2)
    assert harness.result.outcome == QrOutcome.FAILED
    assert "未能在登录页上获取二维码" in harness.result.message
    assert harness.http["n"] == 0


def test_verification_required_is_reported_once_and_not_mistaken_for_failure(monkeypatch):
    harness = _run(monkeypatch, uri="", probe={"captchaNodes": 3}, deadline=0.3)
    assert harness.result.outcome == QrOutcome.VERIFICATION_REQUIRED
    assert [s for s, _ in harness.states] == [QrOutcome.VERIFICATION_REQUIRED]
    assert "安全验证" in harness.result.message
    assert harness.http["n"] == 0


# ----------------------------------------------------------------------
# 正常确认与归属核验
# ----------------------------------------------------------------------
def test_happy_path_confirms_with_server_issued_pass_id(monkeypatch):
    harness = _run(monkeypatch, statuses=(1, 2, 3))
    result = harness.result
    assert result.outcome == QrOutcome.CONFIRMED, result.message
    assert result.cookies["PASS_ID"] == "test-only-pass"
    assert result.cookies["_nano_fp"] == "test-only-nano"
    assert result.identity["shop_id"] == 123
    assert result.qr_png_b64.startswith(PNG_B64_PREFIX)
    assert result.qr_rotation == 1
    assert result.extra["mint"]["exit_confirmed"] is True
    assert QrOutcome.SCANNED in [s for s, _ in harness.states]
    # 身份核验拿到的是新 PASS_ID + 浏览器导出的 Cookie，而不是旧 Cookie。
    assert harness.verified and harness.verified[0]["PASS_ID"] == "test-only-pass"


def test_poll_requests_carry_no_anti_content_and_use_ticket(monkeypatch):
    harness = _run(monkeypatch, statuses=(3,))
    request = harness.http["requests"][0]
    assert "anti-content" not in request.headers
    assert "etag" not in request.headers
    body = json.loads(request.content)
    assert body["data"] == "newTicket$test-only-ticket"
    assert body["fingerprint"] == {"navigator": {"ua": UA}}
    cookie_header = request.headers.get("cookie", "")
    assert "_nano_fp=test-only-nano" in cookie_header
    assert "api_uid=test-only-uid" in cookie_header


def test_cookies_rotated_during_polling_are_delivered(monkeypatch):
    """轮询期间平台轮换的反爬 Cookie 必须随登录态一起交付，不能只给 mint 时的快照。"""
    harness = _run(monkeypatch, statuses=(1, 3), rotate_cookie=True)
    assert harness.result.outcome == QrOutcome.CONFIRMED, harness.result.message
    assert harness.result.cookies["api_uid"] == "rotated-test-only"
    assert harness.result.cookies["PASS_ID"] == "test-only-pass"
    assert harness.result.cookies["_nano_fp"] == "test-only-nano"


def test_identity_mismatch_never_delivers_cookies(monkeypatch):
    harness = _run(monkeypatch, identity={"shop_id": 999, "user_id": 456})
    assert harness.result.outcome == QrOutcome.FAILED
    assert "身份核验未通过" in harness.result.message
    assert harness.result.cookies is None


def test_confirmation_shop_mismatch_is_rejected_before_identity(monkeypatch):
    harness = _run(monkeypatch, expected="999")
    assert harness.result.outcome == QrOutcome.FAILED
    assert "店铺与目标店铺不一致" in harness.result.message
    assert harness.result.cookies is None
    assert harness.verified == []


def test_expected_shop_matches_when_identity_reports_it(monkeypatch):
    harness = _run(monkeypatch, expected="123",
                   identity={"shop_id": 123, "user_id": 456})
    assert harness.result.outcome == QrOutcome.CONFIRMED


def test_identity_shop_mismatch_against_expected_target_is_rejected(monkeypatch):
    """确认响应的 mallId 与身份接口都能对上目标店铺才算通过。"""
    harness = _run(monkeypatch, expected="123",
                   identity={"shop_id": 999, "user_id": 456})
    assert harness.result.outcome == QrOutcome.FAILED
    assert harness.result.cookies is None


# ----------------------------------------------------------------------
# 平台自述失败、限登与传输失败
# ----------------------------------------------------------------------
def test_platform_status_5_surfaces_platform_message(monkeypatch):
    harness = _run(monkeypatch, statuses=(5,), status5_message="平台自述原因-测试")
    assert harness.result.outcome == QrOutcome.FAILED
    assert harness.result.message == "平台自述原因-测试"
    assert harness.result.cookies is None


def test_login_restricted_is_not_delivered_as_success(monkeypatch):
    harness = _run(monkeypatch, statuses=(3,), login_restricted=True)
    assert harness.result.outcome == QrOutcome.FAILED
    assert "限登" in harness.result.message
    assert harness.result.cookies is None
    assert harness.verified == []


def test_transient_failures_are_retried_then_succeed(monkeypatch):
    harness = _run(monkeypatch, statuses=(3,), errors=2)
    assert harness.result.outcome == QrOutcome.CONFIRMED, harness.result.message
    assert harness.http["n"] == 3


def test_persistent_transport_failure_gives_up(monkeypatch):
    harness = _run(monkeypatch, statuses=(3,), errors=99, deadline=2.0)
    assert harness.result.outcome == QrOutcome.FAILED
    assert "网络持续不可用" in harness.result.message
    assert harness.http["n"] == qr_hybrid._MAX_TRANSIENT_FAILURES
    assert harness.result.cookies is None


def test_http_result_never_exposes_ticket_or_cookie_values(monkeypatch):
    harness = _run(monkeypatch, statuses=(3,))
    rendered = repr(harness.result.extra)
    assert "test-only-pass" not in rendered
    assert "newTicket$" not in rendered


def test_cancel_on_confirmation_never_delivers_credentials(monkeypatch):
    cancel = asyncio.Event()
    harness = _run(monkeypatch, statuses=(3,), cancel_event=cancel,
                   http_on_call=lambda *_: cancel.set())
    assert harness.result.outcome == QrOutcome.CANCELLED
    assert harness.result.cookies is None
    assert harness.verified == []


def test_cookie_transfer_preserves_scope_and_expiry():
    mint = qr_hybrid._MintResult(cookie_items=[
        {"name": "ok", "value": "fixture", "domain": ".pinduoduo.com", "path": "/",
         "secure": True, "httpOnly": True, "expires": time.time() + 120},
        {"name": "narrow", "value": "fixture", "domain": "mms.pinduoduo.com", "path": "/janus"},
        {"name": "foreign", "value": "fixture", "domain": "example.com", "path": "/"},
        {"name": "expired", "value": "fixture", "domain": ".pinduoduo.com", "path": "/", "expires": 1},
    ])
    with httpx.Client() as client:
        qr_hybrid._seed_cookies(client, mint)
        assert qr_hybrid._root_cookies(client) == {"ok": "fixture"}
        assert {c.name for c in client.cookies.jar} == {"ok", "narrow"}
        cookie = next(c for c in client.cookies.jar if c.name == "ok")
        assert cookie.secure and cookie.expires and cookie.has_nonstandard_attr("HttpOnly")


def test_cancel_during_identity_never_delivers_credentials(monkeypatch):
    cancel = asyncio.Event()
    def identity(_cookies):
        cancel.set()
        return {"shop_id": 123, "user_id": 456}
    harness = _run(monkeypatch, identity=identity, cancel_event=cancel)
    assert harness.result.outcome == QrOutcome.CANCELLED
    assert harness.result.cookies is None


def test_expiry_during_identity_never_delivers_credentials(monkeypatch):
    def identity(_cookies):
        time.sleep(.12)
        return {"shop_id": 123, "user_id": 456}
    harness = _run(monkeypatch, identity=identity, deadline=.1)
    assert harness.result.outcome == QrOutcome.EXPIRED
    assert harness.result.cookies is None


def test_warm_path_skips_driver_start_and_still_destroys_browser(monkeypatch):
    harness = _run(monkeypatch, launcher="native", warm=True)
    assert harness.result.outcome == QrOutcome.CONFIRMED
    assert "warm_browser_acquired" in harness.result.extra["timings_ms"]
    assert harness.native.close_calls >= 1
    assert harness.native.confirm_calls == 1
    assert harness.playwright.stopped
