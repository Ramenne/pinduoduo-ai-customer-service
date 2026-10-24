# -*- coding: utf-8 -*-
"""
websocket 扫码登录测试（本轮新增）
================================
覆盖两块，**全部使用测试替身，不启动浏览器、不触网、不接触任何真实账号**：

1. 页面状态判据 ``login.qr_state``：尤其是「已扫码待确认 / 需要人工验证 / 已离开
   登录页」三者的区分。回归用例来自本轮真实踩过的坑 —— 商家后台首页文案本身含
   「风控 / 防控」等词，若先判验证关键词、后判是否离开登录页，会把一次成功登录
   误判成需要人工验证。
2. 会话注册表 ``login.qr_session_store``：归属隔离、凭据一次性领取、取消与终态
   资源清理。

运行方式：本仓库 websocket 测试为纯单测风格（无 TestClient 先例），故用
``asyncio.run`` 驱动协程，不引入 pytest-asyncio 依赖。
"""
from __future__ import annotations

import asyncio
import os
import sys

# 路径配置：websocket 服务目录与仓库根目录均需可导入（与既有 conftest 一致口径）。
_WS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_REPO_ROOT = os.path.dirname(_WS_DIR)
for _path in (_REPO_ROOT, _WS_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from login import qr_session_store  # noqa: E402
from login.qr_state import (  # noqa: E402
    QrBrowserResult,
    QrOutcome,
    PageProbe,
    classify_page,
)


def test_reopen_resumes_same_owner_target_but_never_another_session(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(qr_session_store, 'get_settings', lambda: SimpleNamespace(
        qr_login_max_concurrent=1, qr_login_timeout_ms=180000))
    launches=[]
    async def runner(*args):
        launches.append(args)
        await asyncio.Event().wait()
    monkeypatch.setattr(qr_session_store, '_runner', runner)
    async def scenario():
        qr_session_store.reset_for_test()
        try:
            first,ok=await qr_session_store.create_session(7,'shop-a')
            assert ok
            await asyncio.sleep(0)
            resumed,ok=await qr_session_store.create_session(7,'shop-a')
            assert ok and resumed['session_id']==first['session_id']
            assert len(launches)==1
            for owner,target in [(8,'shop-a'),(7,'shop-b'),(7,None)]:
                denied,ok=await qr_session_store.create_session(owner,target)
                assert not ok and 'session_id' not in denied
        finally:
            qr_session_store.reset_for_test()
            await asyncio.sleep(0)
    asyncio.run(scenario())


def test_create_sweeps_expired_session_before_checking_capacity(monkeypatch):
    from datetime import timedelta
    from types import SimpleNamespace
    monkeypatch.setattr(qr_session_store, 'get_settings', lambda: SimpleNamespace(
        qr_login_max_concurrent=1, qr_login_timeout_ms=180000))
    async def runner(*args):
        await asyncio.Event().wait()
    monkeypatch.setattr(qr_session_store, '_runner', runner)
    async def scenario():
        qr_session_store.reset_for_test()
        try:
            first,ok=await qr_session_store.create_session(7,None)
            old=qr_session_store._SESSIONS[first['session_id']]
            old.expires_at=qr_session_store.now_beijing_naive()-timedelta(seconds=1)
            second,ok=await qr_session_store.create_session(7,None)
            assert ok and second['session_id']!=first['session_id']
            assert old.state==QrOutcome.EXPIRED
            await asyncio.sleep(0)
            assert old.task.cancelled()
        finally:
            qr_session_store.reset_for_test()
            await asyncio.sleep(0)
    asyncio.run(scenario())


# ----------------------------------------------------------------------
# 1. 页面状态判据
# ----------------------------------------------------------------------
def test_verification_requires_login_page_and_slider_nodes():
    """需要人工验证：必须同时满足「仍在登录页」与「存在滑块结构节点」。"""
    on_login_with_slider = PageProbe(captcha_nodes=3, on_login_page=True)
    assert on_login_with_slider.needs_verification is True

    # 关键回归：登录成功后停在首页，即便文案命中验证类关键词也不得判为需要验证。
    on_home_page = PageProbe(captcha_nodes=0, on_login_page=False, left_login_page=True,
                             scanned_text=False, expired_text=False)
    assert on_home_page.needs_verification is False


def test_scanned_hint_only_valid_while_on_login_page():
    """「已扫码待确认」只在登录页内成立，且它不是成功判据。"""
    assert PageProbe(scanned_text=True, on_login_page=True).scanned_hint is True
    assert PageProbe(scanned_text=True, on_login_page=False).scanned_hint is False


def test_terminal_states_are_distinct_and_enumerated():
    """终态必须互斥且可枚举：确认 / 领取 / 过期 / 取消 / 失败 不得合并成一个"失败"。"""
    terminal = QrOutcome.terminal()
    assert terminal == frozenset({QrOutcome.CONFIRMED, QrOutcome.CLAIMED, QrOutcome.EXPIRED,
                                  QrOutcome.CANCELLED, QrOutcome.FAILED})
    assert QrOutcome.is_terminal(QrOutcome.CONFIRMED) is True
    assert QrOutcome.is_terminal(QrOutcome.SCANNED) is False


def test_verification_required_is_recoverable_not_terminal():
    """需要人工验证 **不是** 终态：用户本人完成验证后流程要能继续走到成功。

    把它做成终态等于宣布扫码不可用，而事实只是"需要用户做一次人工操作"。
    """
    assert QrOutcome.is_terminal(QrOutcome.VERIFICATION_REQUIRED) is False
    assert QrOutcome.needs_user_action(QrOutcome.VERIFICATION_REQUIRED) is True
    assert QrOutcome.needs_user_action(QrOutcome.WAITING) is False


def test_from_raw_tolerates_missing_fields():
    """页面探测返回缺字段时按默认值处理，不抛异常（健壮性兜底）。"""
    probe = PageProbe.from_raw({"captchaNodes": 2})
    assert probe.captcha_nodes == 2
    assert probe.on_login_page is True
    assert PageProbe.from_raw(None).captcha_nodes == 0


class _FakePage:
    """Playwright 页面的最小替身。"""

    def __init__(self, raw=None, raise_error=False):
        self._raw = raw or {}
        self._raise = raise_error
        self.received_selectors = None

    async def evaluate(self, script, selectors):  # noqa: ARG002
        if self._raise:
            raise RuntimeError("页面重绘中")
        self.received_selectors = selectors
        return self._raw


async def _classify(page):
    return await classify_page(page)


def test_classify_page_maps_probe_and_never_raises():
    """探测正常映射，且探测异常降级为「无信号」而不是抛出。"""
    page = _FakePage({"captchaNodes": 1, "onLoginPage": True, "scannedText": True})
    probe = asyncio.run(_classify(page))
    assert probe.captcha_nodes == 1 and probe.scanned_text is True
    # 传给页面的必须是结构选择器（不是文案匹配）。
    assert ".captcha-wrapper" in page.received_selectors

    broken = asyncio.run(_classify(_FakePage(raise_error=True)))
    assert broken.captcha_nodes == 0 and broken.needs_verification is False


# ----------------------------------------------------------------------
# 2. 会话注册表
# ----------------------------------------------------------------------
def _make_session(session_id: str = "s-1", owner: int = 7, target=None):
    """直接构造一个进行中的会话（跳过浏览器流程，聚焦注册表语义）。"""
    return qr_session_store.QrLoginSession(
        session_id=session_id,
        owner_user_id=owner,
        target_shop_id=target,
        qr_png_b64="QUJD",
        qr_rotation=1,
        state=QrOutcome.WAITING,
    )


def _with_session(session):
    """在注册表中注入会话并保证测试后清理。"""
    qr_session_store.reset_for_test()
    qr_session_store._SESSIONS[session.session_id] = session
    return session


def test_other_user_cannot_read_session():
    """归属不符一律「不存在」，不区分「存在但无权」，避免泄露他人会话存在性。"""
    _with_session(_make_session(owner=7))
    assert asyncio.run(qr_session_store.get_session("s-1", 7)) is not None
    assert asyncio.run(qr_session_store.get_session("s-1", 8)) is None
    assert asyncio.run(qr_session_store.get_session("nope", 7)) is None


def test_claim_is_once_only_and_drops_credentials():
    """凭据只能领取一次，领取后内存中立即丢弃，重复领取返回 already_claimed。"""
    session = _make_session()
    session.state = QrOutcome.CONFIRMED
    session._cookies = {"PASS_ID": "fake"}
    session.shop_id = "123"
    _with_session(session)

    data, reason = asyncio.run(qr_session_store.claim_session("s-1", 7))
    assert reason == "ok" and data["shop_id"] == "123"
    assert session.state == QrOutcome.CLAIMED
    assert session._cookies is None

    data2, reason2 = asyncio.run(qr_session_store.claim_session("s-1", 7))
    assert reason2 == "already_claimed" and data2 is None


def test_claim_rejected_before_confirmation():
    """未确认完成时不得领取凭据。"""
    _with_session(_make_session())
    data, reason = asyncio.run(qr_session_store.claim_session("s-1", 7))
    assert data is None and reason == "not_ready"


def test_cancel_is_owner_checked_and_releases_image():
    """取消：归属校验 + 置终态 + 清空二维码图片。"""
    session = _with_session(_make_session())
    ok, reason = asyncio.run(qr_session_store.cancel_session("s-1", 8))
    assert ok is False and reason == "not_found"

    ok, reason = asyncio.run(qr_session_store.cancel_session("s-1", 7))
    assert ok is True and session.state == QrOutcome.CANCELLED
    assert session.qr_png_b64 is None

    # 已终态的会话不可再次取消。
    ok, reason = asyncio.run(qr_session_store.cancel_session("s-1", 7))
    assert ok is False and reason == "terminal"


def test_terminal_result_clears_qr_image():
    """浏览器流程进入终态后，会话上的二维码图片必须被清掉（图片本身即登录入口）。"""
    session = _with_session(_make_session())
    qr_session_store._apply_result(
        session,
        QrBrowserResult(outcome=QrOutcome.EXPIRED, message="已过期", qr_png_b64="REFT"),
    )
    assert session.state == QrOutcome.EXPIRED
    assert session.qr_png_b64 is None


def test_confirmed_result_keeps_credentials_until_claim():
    """确认完成后凭据短暂驻留，但对外视图绝不含 Cookie。"""
    session = _with_session(_make_session())
    qr_session_store._apply_result(
        session,
        QrBrowserResult(
            outcome=QrOutcome.CONFIRMED,
            message="成功",
            cookies={"PASS_ID": "fake"},
            identity={"shop_id": 999, "shop_name": "店铺", "user_name": "pdd_x"},
        ),
    )
    assert session.shop_id == "999" and session.username == "pdd_x"
    view = session.public_view()
    assert "cookies" not in view and "_cookies" not in view
    assert view["state"] == QrOutcome.CONFIRMED


def test_public_view_excludes_credentials_entirely():
    """对外视图字段白名单校验：只含状态与图片，不含任何凭据键。"""
    view = _make_session().public_view()
    forbidden = {"cookies", "cookie", "password", "pass_id", "token", "_cookies"}
    assert forbidden.isdisjoint(set(view.keys()))


def test_runner_routes_hybrid_and_preserves_target_and_claim(monkeypatch):
    from types import SimpleNamespace
    from login import qr_hybrid
    calls = []
    monkeypatch.setattr(qr_session_store, "get_settings", lambda: SimpleNamespace(
        qr_login_transport="hybrid", qr_login_max_concurrent=1, qr_login_timeout_ms=180000))
    async def hybrid(**options):
        calls.append(options["expected_shop_id"])
        await options["on_page_ready"](None)
        return QrBrowserResult(outcome=QrOutcome.CONFIRMED, cookies={"PASS_ID": "fixture"},
                               identity={"shop_id": "shop-a", "user_id": "fixture"})
    monkeypatch.setattr(qr_hybrid, "run_qr_hybrid", hybrid)
    async def scenario():
        qr_session_store.reset_for_test()
        try:
            view, ok = await qr_session_store.create_session(7, "shop-a")
            assert ok
            session = qr_session_store._SESSIONS[view["session_id"]]
            await session.task
            assert calls == ["shop-a"]
            assert session.state == QrOutcome.CONFIRMED
            assert session.owner_user_id == 7
        finally:
            qr_session_store.reset_for_test()
    asyncio.run(scenario())


def test_password_session_keeps_credentials_private_and_claims_once(monkeypatch):
    from types import SimpleNamespace
    from login import password_session
    monkeypatch.setattr(qr_session_store, 'get_settings', lambda: SimpleNamespace(
        qr_login_max_concurrent=1, qr_login_timeout_ms=180000))
    async def runner(**options):
        assert options['username'] == 'fixture-user' and options['password'] == 'fixture-secret'
        return QrBrowserResult(outcome=QrOutcome.CONFIRMED,
                               cookies={'PASS_ID': 'fixture-cookie'},
                               identity={'shop_id': 'shop-a', 'user_id': 'user-a'})
    monkeypatch.setattr(password_session, 'run_password_session', runner)
    async def scenario():
        qr_session_store.reset_for_test()
        try:
            view, ok = await qr_session_store.create_session(7, None, login_mode='password',
                            username='fixture-user', password='fixture-secret', remark='fixture-remark')
            assert ok and view['login_mode'] == 'password'
            session = qr_session_store._SESSIONS[view['session_id']]
            assert 'fixture-secret' not in repr(session)
            await session.task
            assert session._login_credentials is None
            public = await qr_session_store.get_session(view['session_id'], 7)
            assert 'fixture-secret' not in str(public) and 'fixture-cookie' not in str(public)
            assert await qr_session_store.get_session(view['session_id'], 8) is None
            denied, reason = await qr_session_store.claim_session(view['session_id'], 8)
            assert denied is None and reason == 'not_found'
            payload, reason = await qr_session_store.claim_session(view['session_id'], 7)
            assert reason == 'ok' and payload['password'] == 'fixture-secret'
            assert payload['username'] == 'fixture-user' and payload['remark'] == 'fixture-remark'
            assert session._password is None
            payload, reason = await qr_session_store.claim_session(view['session_id'], 7)
            assert payload is None and reason == 'already_claimed'
        finally:
            qr_session_store.reset_for_test()
    asyncio.run(scenario())
