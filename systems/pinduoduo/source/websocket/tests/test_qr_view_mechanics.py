# -*- coding: utf-8 -*-
"""浏览器辅助窗口的机制测试（真实无头 Playwright，不触登录、不用凭据）。

覆盖任务二的机械部分：
1. frame() 能取到当前登录页画面；
2. 归一化坐标经 input_event 转发后，点击确实落在目标元素上（缩放无关）；
3. 状态变化期间窗口不失效（frame 持续可用）；
4. detach 后 frame/input 立即不可用，资源释放。

不覆盖需要真人的部分（扫码、滑块），那些在真实闭环里由用户完成。
"""
import asyncio
import os
import sys

_WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ROOT = os.path.dirname(_WS)
for p in (_ROOT, _WS):
    if p not in sys.path:
        sys.path.insert(0, p)

import pytest

from login import qr_browser, qr_session_store, qr_view


@pytest.fixture()
def session_id():
    qr_session_store.reset_for_test()
    return "view-test"


def _make_session(session_id, owner=7):
    from datetime import timedelta

    from common.utils.time_utils import now_beijing_naive

    s = qr_session_store.QrLoginSession(
        session_id=session_id, owner_user_id=owner, target_shop_id=None,
        expires_at=now_beijing_naive() + timedelta(minutes=3))
    qr_session_store._SESSIONS[session_id] = s
    return s


def test_missing_expiry_denies_window(session_id):
    """没有过期时间的会话不得开放交互窗口（失败即关闭）。"""
    async def scenario():
        _make_session(session_id).expires_at = None
        assert await qr_view.frame(session_id, 7) is None
    asyncio.run(scenario())


async def _real_page_flow(session_id):
    """用本地 HTML 替身验证输入机制；所有浏览器请求均拦截，不访问平台。"""
    from playwright.async_api import async_playwright

    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=True)
    ctx = await browser.new_context(viewport={"width": 900, "height": 700})
    async def fixture_page(route):
        # 必须带 charset=utf-8：否则浏览器按 Latin-1 解码，中文标签会变成乱码，
        # 页面自身的中文断言（按文案定位元素）就会失败。
        await route.fulfill(content_type="text/html; charset=utf-8", body='''<!doctype html>
          <div class="Common_item_test" onclick="document.querySelector('input').hidden=false">账号登录</div>
          <input type="password" hidden>
          <div style="height:3000px;width:3000px">local fixture</div>''')
    await ctx.route("**/*", fixture_page)
    page = await ctx.new_page()
    await page.goto("https://mms.pinduoduo.com/login/?login_tab=qrcode",
                    wait_until="domcontentloaded", timeout=45000)
    await qr_view.attach(session_id, page)
    return pw, browser, ctx, page


async def _teardown(handles):
    pw, browser, ctx, page = handles
    await qr_view.detach("view-test")
    for closer in (ctx.close, browser.close, pw.stop):
        try:
            await closer()
        except Exception:
            pass


def test_frame_returns_login_page_image(session_id):
    async def scenario():
        _make_session(session_id)
        handles = await _real_page_flow(session_id)
        try:
            frame = await qr_view.frame(session_id, 7)
            assert frame is not None, "frame 不应为 None"
            import base64
            raw = base64.b64decode(frame["image"])
            assert raw[:3] == b"\xff\xd8\xff", "应为 JPEG"
            assert frame["width"] == 900 and frame["height"] == 700
            # 归属不符者拿不到画面
            assert await qr_view.frame(session_id, 8) is None
        finally:
            await _teardown(handles)
    asyncio.run(scenario())


def test_normalized_click_hits_target_under_scaling(session_id):
    """归一化坐标必须与视口尺寸无关地落到同一元素上。"""
    async def scenario():
        _make_session(session_id)
        handles = await _real_page_flow(session_id)
        try:
            page = handles[3]
            password = page.locator("input[type='password']")
            assert not await password.is_visible()
            # 找到「账号登录」标签的中心，换算成归一化坐标后走 input_event 转发
            box = await page.evaluate("""() => {
                const el = Array.from(document.querySelectorAll('div[class^="Common_item"]'))
                    .find(e => e.textContent.trim() === '账号登录');
                if (!el) return null;
                const r = el.getBoundingClientRect();
                return {x: (r.left + r.width / 2), y: (r.top + r.height / 2)};
            }""")
            assert box, "登录页应存在账号登录标签"
            vp = page.viewport_size
            ok = await qr_view.input_event(session_id, 7, {
                "kind": "down", "x": box["x"] / (vp["width"] - 1),
                "y": box["y"] / (vp["height"] - 1)})
            assert ok is True
            await qr_view.input_event(session_id, 7, {
                "kind": "up", "x": box["x"] / (vp["width"] - 1),
                "y": box["y"] / (vp["height"] - 1)})
            assert await password.is_visible(), "输入转发必须使密码框由隐藏变为可见"
            assert await qr_view.input_event(session_id, 7, {
                "kind": "wheel", "x": .5, "y": .5, "delta_x": 0, "delta_y": 450})
            await page.wait_for_function("window.scrollY > 0")
            # 越界与非法输入必须被拒
            assert await qr_view.input_event(session_id, 7,
                                             {"kind": "down", "x": 1.5, "y": 0.5}) is False
            assert await qr_view.input_event(session_id, 7,
                                             {"kind": "key", "key": "a"}) is False
        finally:
            await _teardown(handles)
    asyncio.run(scenario())


def test_frame_survives_state_changes_and_detach_closes(session_id):
    async def scenario():
        s = _make_session(session_id)
        handles = await _real_page_flow(session_id)
        try:
            for state in ("waiting", "scanned", "verification_required"):
                s.state = state
                assert await qr_view.frame(session_id, 7) is not None, state
            # 终态后窗口必须关闭，不再吐画面
            s.state = "confirmed"
            assert await qr_view.frame(session_id, 7) is None
        finally:
            await _teardown(handles)
    asyncio.run(scenario())
