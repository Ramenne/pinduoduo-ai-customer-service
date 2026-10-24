# -*- coding: utf-8 -*-
"""login.qr_hybrid —— 浏览器只取码、HTTP 承接的混合扫码流程。

设计边界：
- 浏览器只负责让官方页面按正常流程 mint 二维码票据；不拦截、不改写平台请求，
  不注入隐藏自动化参数，不代过验证码。
- 取码后导出会话并确认浏览器退出，再展示二维码，避免手机确认与浏览器轮询竞态。
- 浏览器退出确认通过后才开始 HTTP 轮询；票据与 Cookie（含 HttpOnly）由浏览器上下文
  导出，之后不再依赖浏览器。
- 轮询复用 ``QrProtocol`` 的既有校验：status=5、限登、PASS_ID 唯一性、目标店铺比对。
- 取消与截止时间在 mint 与 HTTP 两段都生效；任何失败都不交付 Cookie。
- 账号归属：``QrProtocol`` 先比对确认响应的 mallId，身份核验后再比对身份接口返回的
  shop_id；调用方还可设置 ``expected_shop_id`` 做第三道闸门。

由配置接入 qr_session_store；票据过期后明确结束，由用户重新申请独立会话。
所有日志只记录状态、长度与名称，不记录 Cookie/ticket 值。
"""
from __future__ import annotations

import asyncio
import logging
import time
from http.cookiejar import Cookie
from http.cookies import SimpleCookie
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, Optional
from urllib.parse import parse_qs, urlsplit

import httpx
from playwright.async_api import async_playwright

from channel_pdd.pdd_login import extract_login_identity
from common.core.config import get_settings
from login.native_chrome import NativeChrome
from login.qr_warm_browser import borrow_warm_browser, replenish_warm_browser
from login.qr_browser import (
    DEFAULT_QR_LOGIN_URL,
    VERIFICATION_MESSAGE,
    _QrResponseWatcher,
    _inject_verification_cookies,
    _launch_options,
    _refresh_verification_cache,
    _render_qr_png,
)
from login.qr_protocol import (
    CaptchaRequired,
    LoginRestricted,
    ProtocolError,
    QrProtocol,
    Reply,
    TransportError,
)
from login.qr_state import (
    MMS_COOKIES_URL,
    PASS_ID_COOKIE,
    QrBrowserResult,
    QrOutcome,
    classify_page,
)

logger = logging.getLogger("login.qr_hybrid")

MMS_ORIGIN = "https://mms.pinduoduo.com"
QUERY_PATH = "/janus/api/scan/login/query"

# 浏览器端等待二维码响应的轮询粒度（响应到达会立即唤醒，不依赖该值）。
_MINT_POLL_SECONDS = 1.0
# 关闭浏览器后等待“确认退出”的上限。
_EXIT_CONFIRM_SECONDS = 5.0
# HTTP 轮询的单次超时与间隔。
_HTTP_TIMEOUT_SECONDS = 8.0
_POLL_INTERVAL_SECONDS = 1.5
# 连续传输失败达到该次数即停止，避免网络中断时一直等到会话过期。
_MAX_TRANSIENT_FAILURES = 5

_QrCallback = Callable[[str, int], Awaitable[None]]
_StateCallback = Callable[[str, str], Awaitable[None]]
_PageCallback = Callable[[Any], Awaitable[None]]


class _TransferCancelled(Exception):
    """取消信号在 HTTP 发送前生效；用于把取消与网络失败区分开。"""


@dataclass(repr=False)
class _MintResult:
    """浏览器取码段的结果；terminal_outcome 非空表示未进入 HTTP 段。"""

    terminal_outcome: str = ""
    message: str = ""
    uri: str = ""
    ticket: str = ""
    user_agent: str = ""
    cookies: Dict[str, str] = field(default_factory=dict, repr=False)
    cookie_items: list = field(default_factory=list, repr=False)
    qr_png_b64: Optional[str] = None
    exit_confirmed: bool = False
    timings: Dict[str, int] = field(default_factory=dict)


async def _emit_state(callback: Optional[_StateCallback], state: str, message: str) -> None:
    """推送可恢复中间态；回调失败不影响主流程。"""
    if callback is None:
        return
    try:
        await callback(state, message)
    except Exception as exc:  # noqa: BLE001
        logger.warning("状态回调失败：%s", exc)


async def _emit_qr(callback: Optional[_QrCallback], png_b64: str, rotation: int) -> None:
    """发布二维码；回调失败只告警，图片仍在最终结果里。"""
    if callback is None:
        return
    try:
        await callback(png_b64, rotation)
    except Exception as exc:  # noqa: BLE001
        logger.warning("二维码回调失败：%s", exc)


def _note(timings: Dict[str, int], name: str, started: float) -> None:
    """记录相对起点的毫秒耗时（只记时间，不记内容）。"""
    timings[name] = round((time.monotonic() - started) * 1000)
    logger.info("混合扫码阶段 stage=%s elapsed_ms=%s", name, timings[name])


def _extract_ticket(uri: str) -> str:
    """从官方 uri 中取出唯一票据；格式不符返回空串。"""
    if not isinstance(uri, str) or len(uri) > 4096:
        return ""
    url = urlsplit(uri)
    if url.scheme != "https" or url.hostname != "w.url.cn":
        return ""
    values = parse_qs(url.query).get("data", [])
    if (len(values) != 1 or not values[0].startswith("newTicket$")
            or len(values[0]) <= len("newTicket$")):
        return ""
    return values[0]


async def _close_browser(native: Optional[NativeChrome], browser: Any, context: Any) -> bool:
    """关闭浏览器并确认已退出；返回 False 时调用方必须中止 HTTP 交接。"""
    if native is not None:
        try:
            if not await native.close():
                return False
            return await native.confirm_exited(timeout=_EXIT_CONFIRM_SECONDS)
        except Exception as exc:  # noqa: BLE001 - 退出确认失败按未确认处理
            logger.error("浏览器退出确认异常：%s", exc)
            return False
    try:
        await context.close()
    except Exception:
        pass
    try:
        await browser.close()
    except Exception as exc:  # noqa: BLE001
        logger.error("浏览器关闭异常：%s", exc)
        return False
    return True


async def _mint_over_browser(
    *,
    deadline_at: float,
    cancel_event: asyncio.Event,
    on_qr_update: Optional[_QrCallback],
    on_state_change: Optional[_StateCallback],
    on_page_ready: Optional[_PageCallback],
) -> _MintResult:
    """浏览器取码：确认退出后发布，手机确认只由后续 HTTP 接收。"""
    started = time.monotonic()
    result = _MintResult()
    settings = get_settings()
    login_url = getattr(settings, "qr_login_url", "") or DEFAULT_QR_LOGIN_URL
    playwright = None
    browser = None
    context = None
    native = None
    try:
        launcher = getattr(settings, "qr_login_browser_launcher", "playwright")
        warm = await borrow_warm_browser() if launcher == "native" else None
        if warm is not None:
            playwright, native, browser = warm.playwright, warm.native, warm.browser
            context = browser.contexts[0]
            _note(result.timings, "warm_browser_acquired", started)
        else:
            playwright = await async_playwright().start()
        _note(result.timings, "driver_ready", started)
        if launcher == "native":
            if native is None:
                native = NativeChrome()
                browser = await native.start(playwright.chromium, playwright.chromium.executable_path)
                context = browser.contexts[0]
        elif launcher == "playwright":
            browser = await playwright.chromium.launch(**_launch_options())
            context = await browser.new_context(
                viewport={"width": 1440, "height": 900},
                locale="zh-CN",
                timezone_id="Asia/Shanghai",
            )
            await _inject_verification_cookies(context)
        else:
            result.terminal_outcome = QrOutcome.FAILED
            result.message = "不支持的扫码浏览器启动方式"
            return result
        _note(result.timings, "browser_ready", started)

        if cancel_event.is_set():
            result.terminal_outcome = QrOutcome.CANCELLED
            result.message = "扫码会话已取消"
            return result

        page = await context.new_page()
        watcher = _QrResponseWatcher()
        page.on("response", watcher)
        if on_page_ready is not None:
            await on_page_ready(page)
        await page.goto(login_url, wait_until="domcontentloaded", timeout=max(1, min(45_000, int((deadline_at - time.monotonic()) * 1000))))
        _note(result.timings, "page_dom_ready", started)

        challenge_seen = False
        while time.monotonic() < deadline_at:
            if cancel_event.is_set():
                result.terminal_outcome = QrOutcome.CANCELLED
                result.message = "扫码会话已取消"
                return result
            if watcher.uri:
                break
            probe = await classify_page(page)
            if probe.needs_verification:
                if not challenge_seen:
                    challenge_seen = True
                    await _emit_state(on_state_change, QrOutcome.VERIFICATION_REQUIRED,
                                      VERIFICATION_MESSAGE)
                    logger.warning("平台要求人工安全验证，交由用户本人完成")
            elif probe.expired_hint:
                result.terminal_outcome = QrOutcome.EXPIRED
                result.message = "登录二维码已过期，请重新发起扫码"
                return result
            remaining = max(0.0, deadline_at - time.monotonic())
            await watcher.wait_for_change(min(_MINT_POLL_SECONDS, remaining))

        if not watcher.uri:
            if challenge_seen:
                result.terminal_outcome = QrOutcome.VERIFICATION_REQUIRED
                result.message = VERIFICATION_MESSAGE
            else:
                result.terminal_outcome = QrOutcome.FAILED
                result.message = "未能在登录页上获取二维码，页面结构可能已变更"
            return result

        _note(result.timings, "qr_response_received", started)
        result.uri = watcher.uri
        result.ticket = _extract_ticket(result.uri)
        if not result.ticket:
            result.terminal_outcome = QrOutcome.FAILED
            result.message = "二维码票据格式不正确，未继续轮询"
            return result

        result.qr_png_b64 = _render_qr_png(result.uri)
        if not result.qr_png_b64:
            result.terminal_outcome = QrOutcome.FAILED
            result.message = "二维码生成失败，未继续轮询"
            return result
        _note(result.timings, "qr_image_ready", started)
        items = await context.cookies(MMS_COOKIES_URL)
        if any(i.get("name") == PASS_ID_COOKIE for i in items):
            result.terminal_outcome = QrOutcome.FAILED
            result.message = "取码阶段出现已有登录态，请重新申请扫码"
            return result
        result.cookie_items = [dict(item) for item in items]
        result.cookies = {str(i.get("name")): str(i.get("value") or "")
                          for i in items if i.get("name")}
        user_agent = await page.evaluate("() => navigator.userAgent")
        result.user_agent = user_agent if isinstance(user_agent, str) else ""
        if native is None:
            await _refresh_verification_cache(context)

        _note(result.timings, "cookies_exported", started)
        result.exit_confirmed = await _close_browser(native, browser, context)
        _note(result.timings, "browser_exit_confirmed", started)
        if on_page_ready is not None:
            await on_page_ready(None)
        if result.exit_confirmed and not cancel_event.is_set() and time.monotonic() < deadline_at:
            await _emit_qr(on_qr_update, result.qr_png_b64, 1)
            _note(result.timings, "first_qr_published", started)
        return result
    except Exception as exc:  # noqa: BLE001 - 浏览器段异常按失败收尾
        logger.error("浏览器取码异常 type=%s", type(exc).__name__)
        result.terminal_outcome = QrOutcome.FAILED
        result.message = "扫码登录失败，请稍后重试"
        return result
    finally:
        # close() 已执行时这些调用是幂等的；异常路径下再兜底一次。
        if browser is not None and native is None:
            try:
                await browser.close()
            except Exception:
                pass
        if native is not None:
            try:
                await native.close()
            except Exception:
                pass
        if playwright is not None:
            try:
                await playwright.stop()
            except Exception:
                pass
        replenish_warm_browser()


async def _sleep_or_cancel(cancel_event: asyncio.Event, seconds: float) -> None:
    """按短步长休眠，取消信号可及时生效。"""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if cancel_event.is_set():
            return
        await asyncio.sleep(min(0.2, max(0.0, end - time.monotonic())))


def _interrupted(result, cancel_event, deadline_at):
    if cancel_event.is_set():
        result.outcome = QrOutcome.CANCELLED
        result.message = "扫码会话已取消"
        return True
    if time.monotonic() >= deadline_at:
        result.outcome = QrOutcome.EXPIRED
        result.message = "登录二维码已失效，请重新发起扫码"
        return True
    return False


async def _thread_call(function, *args, cancel_event):
    """不在同步请求尚未退出时关闭其客户端；取消后只清理，绝不应用返回结果。"""
    task = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        cancel_event.set()
        try:
            await asyncio.shield(task)
        except Exception:
            pass
        raise


def _seed_cookies(client, mint):
    """保留域、路径、过期与 Secure，拒绝把浏览器已有登录态带入新一轮确认。"""
    if PASS_ID_COOKIE in mint.cookies:
        raise ProtocolError("交接会话含已有登录态，拒绝继续")
    items = mint.cookie_items or [
        {"name": name, "value": value, "domain": "mms.pinduoduo.com", "path": "/"}
        for name, value in mint.cookies.items()]
    for item in items:
        domain = item.get("domain", "")
        if domain not in {"mms.pinduoduo.com", ".mms.pinduoduo.com",
                          "pinduoduo.com", ".pinduoduo.com"}:
            continue
        name, value = item.get("name"), item.get("value")
        if not isinstance(name, str) or not isinstance(value, str):
            continue
        if name == PASS_ID_COOKIE:
            raise ProtocolError("交接会话含已有登录态，拒绝继续")
        expires = item.get("expires", -1)
        expires = int(expires) if isinstance(expires, (int, float)) and expires > 0 else None
        if expires is not None and expires <= time.time():
            continue
        client.cookies.jar.set_cookie(Cookie(
            version=0, name=name, value=value, port=None, port_specified=False,
            domain=domain, domain_specified=True, domain_initial_dot=domain.startswith('.'),
            path=item.get("path") or "/", path_specified=True,
            secure=bool(item.get("secure", False)), expires=expires, discard=expires is None,
            comment=None, comment_url=None,
            rest={"HttpOnly": None} if item.get("httpOnly") else {}, rfc2109=False))


def _root_cookies(client):
    jar = SimpleCookie()
    jar.load(client.build_request("GET", MMS_COOKIES_URL).headers.get("cookie", ""))
    return {name: item.value for name, item in jar.items()}


def _build_protocol(
    *,
    mint: _MintResult,
    client: httpx.Client,
    last_status: Dict[str, Any],
    cancel_event: asyncio.Event,
    expected_shop_id: Optional[str],
) -> QrProtocol:
    """把导出 Cookie 与票据接到既有协议层；轮询不生成 Anti-Content。"""
    def transport(method: str, path: str, headers: Dict[str, str], body: Dict[str, Any]) -> Reply:
        if cancel_event.is_set():
            raise _TransferCancelled()
        request = client.build_request(method, path, headers=headers, json=body)
        response = client.send(request)
        try:
            payload = response.json()
        except Exception:
            payload = {}
        result = payload.get("result") if isinstance(payload, dict) else None
        last_status["value"] = result.get("status") if isinstance(result, dict) else None
        return Reply(response.status_code, payload,
                     tuple(response.headers.get_list("set-cookie")))

    protocol = QrProtocol(
        transport=transport,
        headers_provider=lambda: {},
        fingerprint={"navigator": {"ua": mint.user_agent}},
        expected_shop_id=expected_shop_id,
        # 2026-10-19 实测：query 不带 Anti-Content/ETag、空指纹也可返回 status=1；
        # 这里明确只对轮询开启缺失开关，取码仍由浏览器完成。
        allow_missing_anti_content=True,
    )
    protocol.adopt_ticket(mint.ticket)
    return protocol


async def _poll_over_http(
    *,
    mint: _MintResult,
    deadline_at: float,
    cancel_event: asyncio.Event,
    expected_shop_id: Optional[str],
    on_state_change: Optional[_StateCallback],
    http_transport: Optional[httpx.BaseTransport],
    started_at: Optional[float] = None,
) -> QrBrowserResult:
    """浏览器退出后，用导出的 Cookie 与票据完成确认和身份核验。"""
    started = time.monotonic() if started_at is None else started_at
    result = QrBrowserResult()
    headers = {"User-Agent": mint.user_agent, "Referer": DEFAULT_QR_LOGIN_URL}
    client = httpx.Client(
        base_url=MMS_ORIGIN,
        timeout=httpx.Timeout(_HTTP_TIMEOUT_SECONDS, connect=5.0),
        follow_redirects=False,
        trust_env=False,
        transport=http_transport,
        headers=headers,
    )
    protocol = None
    try:
        _seed_cookies(client, mint)
        last_status: Dict[str, Any] = {"value": None}
        protocol = _build_protocol(
            mint=mint, client=client, last_status=last_status,
            cancel_event=cancel_event, expected_shop_id=expected_shop_id)
        _note(result.extra.setdefault("timings_ms", {}), "handoff_started", started)
        scanned = False
        transient_failures = 0
        while time.monotonic() < deadline_at:
            if _interrupted(result, cancel_event, deadline_at):
                return result
            timeout = max(.05, min(_HTTP_TIMEOUT_SECONDS, deadline_at - time.monotonic()))
            client.timeout = httpx.Timeout(timeout, connect=min(5.0, timeout))
            try:
                confirmation = await _thread_call(protocol.poll, cancel_event=cancel_event)
            except _TransferCancelled:
                result.outcome = QrOutcome.CANCELLED
                result.message = "扫码会话已取消"
                return result
            except TransportError:
                transient_failures += 1
                if transient_failures >= _MAX_TRANSIENT_FAILURES:
                    result.outcome = QrOutcome.FAILED
                    result.message = "网络持续不可用，已停止轮询且未保存凭据"
                    return result
                await _sleep_or_cancel(cancel_event, _POLL_INTERVAL_SECONDS)
                continue
            except LoginRestricted as exc:
                result.outcome = QrOutcome.FAILED
                result.message = str(exc)
                return result
            except CaptchaRequired:
                result.outcome = QrOutcome.FAILED
                result.message = "轮询被要求图形验证码；本流程不绕过验证码"
                return result
            except ProtocolError as exc:
                result.outcome = QrOutcome.FAILED
                result.message = str(exc) or "扫码轮询失败，未保存凭据"
                return result
            transient_failures = 0
            if _interrupted(result, cancel_event, deadline_at):
                return result

            if confirmation.state == "confirmed_response_only":
                pass_id = confirmation.cookie.split("=", 1)[1]
                # 以本 HTTP 会话的完整 Cookie 罐为准：轮询期间平台可能轮换/新增
                # 反爬 Cookie，只拼 mint 导出值会丢失这些更新。
                cookies = _root_cookies(client)
                if cookies.get(PASS_ID_COOKIE) != pass_id:
                    result.outcome = QrOutcome.FAILED
                    result.message = "本轮登录凭据的作用域不正确，未保存凭据"
                    return result
                _note(result.extra["timings_ms"], "confirmation_received", started)
                identity = await _thread_call(extract_login_identity, cookies,
                                              cancel_event=cancel_event)
                if _interrupted(result, cancel_event, deadline_at):
                    return result
                if (not isinstance(identity, dict)
                        or str(identity.get("shop_id")) != confirmation.shop_id
                        or str(identity.get("user_id")) != confirmation.user_id):
                    result.outcome = QrOutcome.FAILED
                    result.message = "新登录态独立身份核验未通过，未保存凭据"
                    return result
                expected = str(expected_shop_id) if expected_shop_id else None
                if expected and str(identity.get("shop_id")) != expected:
                    result.outcome = QrOutcome.FAILED
                    result.message = "登录账号与目标店铺不一致，未保存凭据"
                    return result
                _note(result.extra["timings_ms"], "identity_verified", started)
                result.outcome = QrOutcome.CONFIRMED
                result.message = "扫码登录成功"
                result.cookies = cookies
                result.identity = identity
                return result

            status = last_status["value"]
            if status == 1:
                await _sleep_or_cancel(cancel_event, _POLL_INTERVAL_SECONDS)
                continue
            if status == 2:
                if not scanned:
                    scanned = True
                    await _emit_state(on_state_change, QrOutcome.SCANNED,
                                      "已扫码，请在手机上确认登录")
                await _sleep_or_cancel(cancel_event, _POLL_INTERVAL_SECONDS)
                continue
            if status == 5:
                result.outcome = QrOutcome.FAILED
                result.message = confirmation.message or "系统繁忙，请通过账密登录"
                return result
            result.outcome = QrOutcome.FAILED
            result.message = "未识别的扫码状态，已停止且未保存凭据"
            return result

        result.outcome = QrOutcome.EXPIRED
        result.message = ("等待手机确认超时，二维码已失效，请重新发起扫码" if scanned
                          else "登录二维码已失效，请重新发起扫码")
        return result
    finally:
        if protocol is not None:
            protocol.close()
        client.cookies.clear()
        client.close()


async def run_qr_hybrid(
    *,
    deadline_at: float,
    cancel_event: asyncio.Event,
    expected_shop_id: Optional[str] = None,
    on_qr_update: Optional[_QrCallback] = None,
    on_state_change: Optional[_StateCallback] = None,
    on_page_ready: Optional[_PageCallback] = None,
    http_transport: Optional[httpx.BaseTransport] = None,
) -> QrBrowserResult:
    """执行混合扫码；结果仍由现有会话的一次性领取流程保存。"""
    started = time.monotonic()
    result = QrBrowserResult()
    mint = None
    try:
        if _interrupted(result, cancel_event, deadline_at):
            return result
        mint = await _mint_over_browser(
            deadline_at=deadline_at, cancel_event=cancel_event,
            on_qr_update=on_qr_update, on_state_change=on_state_change,
            on_page_ready=on_page_ready)
        result.extra["timings_ms"] = dict(mint.timings)
        result.extra["mint"] = {
            "uri_len": len(mint.uri), "ticket_len": len(mint.ticket),
            "cookie_names": sorted(mint.cookies), "exit_confirmed": mint.exit_confirmed}
        if mint.terminal_outcome:
            result.outcome, result.message = mint.terminal_outcome, mint.message
            return result
        if not mint.exit_confirmed:
            result.outcome = QrOutcome.FAILED
            result.message = "浏览器未能确认退出，已中止 HTTP 交接"
            return result
        if _interrupted(result, cancel_event, deadline_at):
            return result
        polled = await _poll_over_http(
            mint=mint, deadline_at=deadline_at, cancel_event=cancel_event,
            expected_shop_id=expected_shop_id, on_state_change=on_state_change,
            http_transport=http_transport, started_at=started)
        polled.extra["timings_ms"] = {**result.extra["timings_ms"],
                                      **polled.extra.get("timings_ms", {})}
        polled.extra["mint"] = result.extra["mint"]
        result = polled
        if result.outcome == QrOutcome.CONFIRMED:
            result.qr_png_b64 = mint.qr_png_b64
            result.qr_rotation = 1
        return result
    except Exception:
        logger.error("混合扫码流程异常，未交付凭据", exc_info=False)
        result = QrBrowserResult()
        if not _interrupted(result, cancel_event, deadline_at):
            result.outcome = QrOutcome.FAILED
            result.message = "扫码交接失败，请重新发起扫码"
        return result
    finally:
        result.elapsed_seconds = round(time.monotonic() - started, 1)
        if mint is not None:
            mint.cookies.clear()
            mint.cookie_items.clear()
            mint.ticket = mint.uri = ""
            mint.qr_png_b64 = None


__all__ = ["run_qr_hybrid"]
