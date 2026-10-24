# -*- coding: utf-8 -*-
"""
login.qr_browser —— 拼多多商家后台「扫码登录」浏览器流程
======================================================
本文件用途：在 websocket 服务内，用**独立的、一次性的**浏览器会话
打开拼多多官方登录页的扫码页，等待用户手机确认，并在确认完成后采集登录凭据。
它是「扫码登录」能力的执行层，不接触数据库、不落库、不写日志凭据。

设计要点（对应本轮的机制层实测结论，2026-10-16）：

1. **会话隔离且完全临时**：Playwright 启动模式每轮新建上下文；native 模式由
   系统直接启动 Chrome，使用本轮独立临时目录及默认上下文，结束后删除目录。
   两种方式都不加载既有店铺的登录凭据。
2. **二维码由我们自己的接口数据生成，不是页面截图**：只读监听官方页面**自己**发出的
   ``/janus/api/scan/login/qrcode`` 响应，取出本轮 ``result.uri``，再用 ``qrcode``
   库重新编码成清晰二维码。绝不把页面上的 canvas 截图当作二维码（那会带上浮层、
   灰暗或被验证控件遮挡），也绝不拦截/改写平台的请求与响应。
3. **免验证取码凭据**：实测在**全新隔离上下文**中只注入
   ``msfe-pc-cookie-captcha-token`` 与 ``api_uid``，无头即可直接取到 ``uri``、不再弹滑块。
   该凭据来自用户**本人完成过一次**的图形验证，只保存在进程内存里，不写库、不落盘。
   没有它时平台返回 54001，流程如实以 ``verification_required`` 呈现，绝不代过验证码。
4. **状态判定以结构信号为准，不用页面标题文案**：
   - 成功：``context.cookies(MMS_ORIGIN)`` 中出现 ``PASS_ID``，**并且** 用该凭据
     调用店铺 / 用户信息接口能取回真实 ``mall_id``（即"验证登录态 + 验证店铺身份"）；
   - 已扫码待确认：页面出现扫码确认提示文案（仅作中间态展示，不作成功判据）；
   - 需要人工验证：页面出现滑块拼图结构节点（``.captcha-wrapper`` 等）；
   - 过期：二维码刷新失败或到达截止时间。
   实测教训：商家后台首页文案本身含「风控 / 防控」等词，先判验证关键词、后判
   是否离开登录页会把一次成功登录误判成「需人工验证」，故本文件把「离开登录页」
   仅作为辅助信号，**最终成功判据是接口核验通过**。
5. **不做任何验证码绕过**：出现人机验证时立即以 ``verification_required`` 返回，
   把决定权交回用户，绝不尝试拖动滑块或调用打码能力。
6. **反检测**：本模块默认 **不** 携带任何隐藏自动化特征的参数。是否传入
   ``--disable-blink-features=AutomationControlled`` 由配置项显式决定，默认关闭。

实现约束（开发规范）：导入置顶（51）、中文注释（37）、文件名用下划线（40）、
单文件 ≤500 行（35）、日志禁用 debug（38）、地址与超时经环境变量（21）、
复用共通登录身份抓取能力（36/52）。
"""
from __future__ import annotations

import asyncio
import base64
import io
import logging
import time
from contextvars import ContextVar
from urllib.parse import urlsplit, parse_qs
from typing import Any, Dict, List, Optional

import qrcode
from playwright.async_api import async_playwright

from channel_pdd.pdd_login import extract_login_identity
from common.core.config import get_settings
from login.native_chrome import NativeChrome
from login.qr_state import (
    MMS_COOKIES_URL,
    PASS_ID_COOKIE,
    QrBrowserResult,
    QrOutcome,
    classify_page,
)

logger = logging.getLogger("login.qr_browser")

# 拼多多官方登录页（扫码页签）。经配置可覆盖，此处仅为官方入口默认值。
DEFAULT_QR_LOGIN_URL: str = "https://mms.pinduoduo.com/login/?login_tab=qrcode"

# 二维码候选选择器：按 specificity 排序。`.qr-code canvas` 已在真实页面实测命中。
QR_SELECTORS: List[str] = [
    ".qr-code canvas",
    ".scan-login canvas",
    "div.qrcode canvas",
    "canvas.qrcode-canvas",
    '[class*="qr-code"] canvas',
    '[class*="QrCode"] canvas',
    '[class*="qrcode"] canvas',
    "img.qrcode-img",
    ".qrcode-img img",
    '[class*="qrcode"] img',
    'img[src*="qrcode"]',
]

# 轮询间隔（秒）：兼顾状态响应速度与平台请求压力。
_POLL_INTERVAL_SECONDS: float = 1.0

# 身份核验的最小间隔（秒）。PASS_ID 出现后手机端可能还没点确认，若每秒都去调平台
# 接口，一次扫码会打出上百次无效请求，反而容易触发风控。故做节流。
_VERIFY_MIN_INTERVAL_SECONDS: float = 5.0

# 二维码元素连续缺失多久（秒）才判失效。实测两个场景都会让元素短暂消失：
# 二维码自身轮换（约 1 个轮询周期），以及用户完成人工验证后整页重载（数秒）。
# 按次数容错会在这两种情况下误杀正常会话（2026-10-16 真实闭环两次踩中），故改为按时间。
_QR_ABSENT_SECONDS: float = 10.0

# 需要人工验证时对用户呈现的固定说明。刻意不写成"失败"，也不承诺系统会代为完成。
VERIFICATION_MESSAGE: str = (
    "平台要求先完成一次人工安全验证（图形/滑块）。本系统不会代您通过验证码，"
    "请由您本人完成本次验证；通过后继续获取二维码，平台仍可能再次要求验证。"
)

# 官方页面自行发出的取码接口路径。只读监听其响应，绝不拦截或改写。
_QRCODE_PATH: str = "/janus/api/scan/login/qrcode"

# 免验证取码凭据。实测（2026-10-16）：在**全新隔离上下文**中只注入这两个 Cookie，
# 无头即可直接取到 uri、不再弹滑块。它们来自用户本人完成的一次图形验证，属于
# 反爬凭据而非登录凭据 —— 不含任何店铺登录态。
_CAPTCHA_COOKIE: str = "msfe-pc-cookie-captcha-token"
_ANON_COOKIE: str = "api_uid"
_CAPTCHA_KEEP: tuple = (_CAPTCHA_COOKIE, _ANON_COOKIE)

# 进程内缓存：只在内存里保存免验证凭据，不写数据库、不落盘、不打日志值。
_CAPTCHA_COOKIES: Dict[str, Dict[str, Any]] = {}
_CAPTCHA_BY_OWNER: Dict[int, Dict[str, Dict[str, Any]]] = {}
verification_owner = ContextVar("qr_verification_owner", default=None)


def _verification_cache():
    owner = verification_owner.get()
    if owner is None:
        return _CAPTCHA_COOKIES  # 离线驱动/测试；生产注册表必须设置归属。
    return _CAPTCHA_BY_OWNER.setdefault(owner, {})


async def _emit_state(callback: Optional[Any], state: str, message: str) -> None:
    """把可恢复的中间状态推给会话注册表（回调失败不中断扫码流程）。"""
    if callback is None:
        return
    try:
        await callback(state, message)
    except Exception as exc:  # noqa: BLE001
        logger.warning("状态更新回调失败：%s", exc)

# 二维码尺寸判据（与实测一致：页面渲染为 120x120）。
_MIN_QR_SIDE: float = 80.0
_MAX_QR_ASPECT_RATIO: float = 1.4


def _launch_options() -> Dict[str, Any]:
    """组装浏览器启动参数（规范 21：全部经配置，不写死）。

    Returns:
        传给 ``chromium.launch`` 的关键字参数。默认不含任何隐藏自动化特征的参数。
    """
    settings = get_settings()
    args: List[str] = []
    if getattr(settings, "qr_login_disable_automation_feature", False):
        # 此参数改变自动化标记，但不能保证平台直接下发二维码。
        # 它属于「隐藏自动化特征」手段，因此必须由配置显式打开，默认关闭。
        args.append("--disable-blink-features=AutomationControlled")
    return {
        "headless": bool(getattr(settings, "qr_login_headless", True)),
        "args": args,
    }


async def _find_qr_element(page: Any) -> Optional[Any]:
    """按候选选择器找到已渲染出内容的二维码元素。"""
    for selector in QR_SELECTORS:
        try:
            element = await page.query_selector(selector)
        except Exception:  # noqa: BLE001 - 选择器在页面重绘期间可能失效
            continue
        if element is None:
            continue
        try:
            box = await element.bounding_box()
        except Exception:  # noqa: BLE001
            box = None
        if not box:
            continue
        if box["width"] < _MIN_QR_SIDE or box["height"] < _MIN_QR_SIDE:
            continue
        ratio = max(box["width"], box["height"]) / max(1.0, min(box["width"], box["height"]))
        if ratio > _MAX_QR_ASPECT_RATIO:
            continue
        return element
    return None


def _render_qr_png(uri: str) -> Optional[str]:
    """把本轮取码响应里的 ``uri`` 重新编码成我们自己的清晰二维码。

    与"截页面图"的区别：这是从接口数据直接编码，尺寸、纠错级别由我们决定，不会
    带上页面浮层、蒙层或验证控件遮挡，也不受页面渲染尺寸（实测仅 120×120）限制。
    """
    try:
        qr = qrcode.QRCode(box_size=10, border=4,
                           error_correction=qrcode.constants.ERROR_CORRECT_M)
        qr.add_data(uri)
        qr.make(fit=True)
        buffer = io.BytesIO()
        qr.make_image().save(buffer, format="PNG")
        return base64.b64encode(buffer.getvalue()).decode("ascii")
    except Exception as exc:  # noqa: BLE001 - 编码失败不中断扫码流程
        logger.error("二维码生成失败：%s", exc)
        return None


class _QrResponseWatcher:
    """只读监听官方页面自己发出的取码响应，取出本轮 ``result.uri``。

    刻意不做 ``page.route`` 拦截或改写：由官方页面按正常流程产生请求，我们只是
    读取它拿到的响应，从而避免伪造指纹或自造请求。
    """

    def __init__(self) -> None:
        self.uri: Optional[str] = None
        self.changed = asyncio.Event()

    async def __call__(self, response: Any) -> None:
        try:
            source = urlsplit(response.url)
            if (source.scheme != "https" or source.hostname != "mms.pinduoduo.com"
                    or source.path != _QRCODE_PATH):
                return
            body = await response.json()
        except Exception:  # noqa: BLE001 - 响应非 JSON / 已释放时忽略
            return
        if not isinstance(body, dict) or body.get("success") is not True:
            return
        result = body.get("result")
        if isinstance(result, dict) and isinstance(result.get("uri"), str) and result["uri"]:
            uri = result["uri"]
            if len(uri) > 4096:
                return
            url = urlsplit(uri)
            tickets = parse_qs(url.query).get("data", [])
            if (url.scheme == "https" and url.hostname == "w.url.cn"
                    and len(tickets) == 1 and tickets[0].startswith("newTicket$")):
                if self.uri != uri:
                    self.uri = uri
                    self.changed.set()

    async def wait_for_change(self, timeout):
        """新二维码响应立即唤醒消费循环；无变化时保留原有页面检查频率。"""
        if timeout <= 0:
            self.changed.clear()
            await asyncio.sleep(0)
            return
        try:
            await asyncio.wait_for(self.changed.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass
        finally:
            # 每个响应只唤醒一次；即使页面仍要求人工验证，也不能空转占满事件循环。
            self.changed.clear()


def remember_verification_cookies(cookies: Any) -> int:
    """把一次人工验证后得到的免验证凭据记入进程内缓存。

    只保留白名单内的名字（反爬凭据 + 匿名设备号），值仅存内存。返回记忆条数。

    Args:
        cookies: Playwright ``context.cookies()`` 的返回值或同构列表。

    Returns:
        实际记入内存的 Cookie 条数。
    """
    kept = 0
    for item in cookies or []:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if name in _CAPTCHA_KEEP and item.get("value"):
            _verification_cache()[str(name)] = dict(item)
            kept += 1
    return kept


def has_verification_cookie() -> bool:
    """当前进程是否已持有免验证取码凭据。"""
    return bool(_verification_cache().get(_CAPTCHA_COOKIE))


def reset_verification_cache_for_test() -> None:
    """清空进程内免验证凭据（仅供测试使用）。"""
    _CAPTCHA_COOKIES.clear()
    _CAPTCHA_BY_OWNER.clear()


async def _inject_verification_cookies(context: Any) -> int:
    """把缓存的免验证凭据注入到本次隔离上下文；没有则返回 0。"""
    cache = _verification_cache()
    for name in list(cache):
        expires = cache[name].get("expires", -1)
        if isinstance(expires, (int, float)) and 0 < expires <= time.time():
            cache.pop(name)
    if not cache:
        return 0
    try:
        await context.add_cookies([dict(item) for item in cache.values()])
        return len(cache)
    except Exception as exc:  # noqa: BLE001 - 注入失败退化为"可能需要人工验证"
        logger.warning("免验证凭据注入失败，将按常规流程取码：%s", exc)
        return 0


async def _refresh_verification_cache(context: Any) -> None:
    """从上下文刷新免验证凭据缓存（为后续扫码会话省去重复人工验证）。"""
    try:
        cookies = await context.cookies(MMS_COOKIES_URL)
    except Exception:  # noqa: BLE001 - 读不到就保持原缓存
        return
    remember_verification_cookies(cookies)


async def _harvest_cookies(context: Any) -> Dict[str, str]:
    """读取商家域下的 Cookie（含 HttpOnly），返回扁平 name->value 字典。

    说明：Playwright 的 ``context.cookies(url)`` 能读到 HttpOnly Cookie，这是
    ``document.cookie`` 做不到的；本函数只取该 URL 实际会携带的 Cookie，避免把
    无关域名的同名 Cookie 混进来。
    """
    try:
        items = await context.cookies(MMS_COOKIES_URL)
    except Exception as exc:  # noqa: BLE001
        logger.warning("读取 Cookie 失败：%s", exc)
        return {}
    collected: Dict[str, str] = {}
    for item in items:
        name = item.get("name")
        if name:
            collected[str(name)] = str(item.get("value") or "")
    return collected


async def _verify_identity(cookies: Dict[str, str]) -> Optional[Dict[str, Any]]:
    """用采集到的凭据核验登录态与店铺身份（复用共通接口，规范 36）。

    只有能取回真实 ``shop_id`` 与 ``user_id`` 的凭据才算登录成功。
    """
    if not cookies:
        return None
    try:
        identity = await asyncio.to_thread(extract_login_identity, cookies)
    except Exception as exc:  # noqa: BLE001 - 核验异常按未通过处理
        logger.error("扫码后身份核验异常：%s", exc)
        return None
    return identity


async def run_qr_login(
    *,
    deadline_at: float,
    cancel_event: asyncio.Event,
    on_qr_update: Optional[Any] = None,
    on_state_change: Optional[Any] = None,
    on_page_ready: Optional[Any] = None,
) -> QrBrowserResult:
    """执行一次完整扫码登录流程。

    Args:
        deadline_at: 本次流程的绝对截止时刻（``time.monotonic()`` 基准）。
        cancel_event: 取消信号；置位后流程以 ``cancelled`` 终态退出并释放资源。
        on_qr_update: 二维码更新回调，签名 ``async (png_b64, rotation) -> None``。
        on_state_change: 可恢复中间态回调，签名 ``async (state, message) -> None``，
            用于把 ``scanned`` / ``verification_required`` 实时推给前端。

    Returns:
        ``QrBrowserResult``；``outcome`` 取 ``QrOutcome`` 中的终态之一。
    """
    settings = get_settings()
    login_url = getattr(settings, "qr_login_url", "") or DEFAULT_QR_LOGIN_URL
    started = time.monotonic()
    result = QrBrowserResult()
    def note_stage(name):
        elapsed_ms = round((time.monotonic() - started) * 1000)
        result.extra.setdefault("timings_ms", {})[name] = elapsed_ms
        logger.info("扫码阶段耗时 stage=%s elapsed_ms=%s", name, elapsed_ms)

    if cancel_event.is_set():
        result.outcome = QrOutcome.CANCELLED
        result.message = "扫码会话已取消"
        return result

    playwright = None
    browser = None
    context = None
    native = None
    try:
        playwright = await async_playwright().start()
        note_stage("driver_ready")
        launcher = getattr(settings, "qr_login_browser_launcher", "playwright")
        if launcher == "native":
            native = NativeChrome()
            browser = await native.start(playwright.chromium, playwright.chromium.executable_path)
        elif launcher == "playwright":
            browser = await playwright.chromium.launch(**_launch_options())
        else:
            raise ValueError("不支持的扫码浏览器启动方式")
        note_stage("browser_ready")
        # 启动浏览器可能耗时数秒，期间用户可能已取消；再查一次避免白开一个浏览器。
        if cancel_event.is_set():
            result.outcome = QrOutcome.CANCELLED
            result.message = "扫码会话已取消"
            return result
        # 一次性上下文：不载入任何既有 storageState，保证与已绑定店铺凭据完全隔离。
        if native is not None:
            # 原生实例已有默认上下文；它的配置目录本轮新建、结束删除。
            # 保持已验证的普通 Chrome 路径，不注入既有验证凭据或历史店铺登录态。
            context = browser.contexts[0]
        else:
            context = await browser.new_context(
                viewport={"width": 1440, "height": 900},
                locale="zh-CN",
                timezone_id="Asia/Shanghai",
            )
            await _inject_verification_cookies(context)
        page = await context.new_page()
        # 只读监听官方页面自己发出的取码响应，取出本轮 uri 供我们自己编码二维码。
        watcher = _QrResponseWatcher()
        page.on("response", watcher)
        if on_page_ready is not None:
            await on_page_ready(page)
        try:
            await page.goto(login_url, wait_until="domcontentloaded", timeout=45_000)
        except Exception as exc:  # noqa: BLE001 - 导航失败降级为失败终态
            logger.error("打开扫码登录页失败：%s", exc)
            result.outcome = QrOutcome.FAILED
            result.message = "无法打开拼多多登录页，请稍后重试"
            return result

        note_stage("page_dom_ready")

        rotation = 0
        last_uri: Optional[str] = None
        last_verify_at = 0.0
        challenge_seen = False
        scanned_seen = False
        qr_ever_seen = False
        qr_absent_since: Optional[float] = None

        while time.monotonic() < deadline_at:
            if cancel_event.is_set():
                result.outcome = QrOutcome.CANCELLED
                result.message = "扫码会话已取消"
                break

            # --- 1. 成功判据：凭据 + 平台身份核验。这是唯一的成功路径。 ---
            #     刻意放在最前面：用户确认成功后页面会立即跳转、二维码元素随之消失，
            #     若先判"元素还在不在"，一次成功扫码会被误报成"二维码已失效"。
            cookies = await _harvest_cookies(context)
            if PASS_ID_COOKIE in cookies and (time.monotonic() - last_verify_at) >= _VERIFY_MIN_INTERVAL_SECONDS:
                last_verify_at = time.monotonic()
                identity = await _verify_identity(cookies)
                if identity:
                    result.outcome = QrOutcome.CONFIRMED
                    result.cookies = cookies
                    result.identity = identity
                    result.message = "扫码登录成功"
                    note_stage("identity_verified")
                    logger.info("扫码登录成功：shop_id=%s（未记录任何凭据内容）",
                                identity.get("shop_id"))
                    break
                # 核验未通过：可能手机端尚未点确认，继续等待而不是放弃。

            # --- 2. 页面状态探测（结构信号优先，文案仅作辅助） ---
            probe = await classify_page(page)

            if probe.needs_verification:
                # 人工验证期间不累计二维码缺失时长；结束后重新计算重载宽限期。
                qr_absent_since = None
                # 可恢复态：把状态推给前端展示，但**不退出**，等用户本人完成验证。
                # 绝不代为拖动滑块或调用任何打码能力。
                if not challenge_seen:
                    challenge_seen = True
                    await _emit_state(on_state_change, QrOutcome.VERIFICATION_REQUIRED,
                                     VERIFICATION_MESSAGE)
                    logger.warning("平台要求人工安全验证，交由用户本人完成")
            else:
                new_uri = watcher.uri if watcher.uri and watcher.uri != last_uri else None
                element = None
                if new_uri is None:
                    element = await _find_qr_element(page)
                if new_uri is not None:
                    # 用本轮接口的 uri 自己编码二维码（清晰、无遮挡、尺寸由我们决定）。
                    last_uri = new_uri
                    qr_ever_seen = True
                    qr_absent_since = None
                    png_b64 = _render_qr_png(new_uri)
                    if png_b64:
                        rotation += 1
                        result.qr_png_b64 = png_b64
                        result.qr_rotation = rotation
                        if on_qr_update is not None:
                            try:
                                await on_qr_update(png_b64, rotation)
                            except Exception as exc:  # noqa: BLE001 - 回调失败不中断扫码
                                logger.warning("二维码更新回调失败：%s", exc)
                        if rotation == 1:
                            note_stage("first_qr_published")
                if new_uri is not None or element is not None:
                    qr_ever_seen = True
                    qr_absent_since = None
                    if probe.scanned_hint and not scanned_seen:
                        scanned_seen = True
                        await _emit_state(on_state_change, QrOutcome.SCANNED,
                                          "已扫码，请在手机上确认登录")
                        logger.info("检测到用户已扫码，等待手机端确认")
                elif probe.left_login_page:
                    qr_absent_since = None
                    # 已离开登录页却始终核验不过：明确失败，不伪装成过期。
                    result.outcome = QrOutcome.FAILED
                    result.message = "页面已跳转但身份核验未通过，未保存任何凭据"
                    # 保留页面供用户关闭跳转后的提示；到期仍未核验才失败。
                else:
                    # 元素缺失：先记录起点，只有连续缺失超过阈值才判失效。
                    if qr_absent_since is None:
                        qr_absent_since = time.monotonic()
                    elif time.monotonic() - qr_absent_since >= _QR_ABSENT_SECONDS:
                        if qr_ever_seen or challenge_seen:
                            result.outcome = QrOutcome.EXPIRED
                            result.message = "登录二维码已失效，请重新发起扫码"
                        else:
                            result.outcome = QrOutcome.FAILED
                            result.message = "未能在登录页上找到登录二维码，页面结构可能已变更"
                        break

            if probe.expired_hint and not challenge_seen:
                result.outcome = QrOutcome.EXPIRED
                result.message = "登录二维码已过期，请重新发起扫码"
                break

            await watcher.wait_for_change(
                min(_POLL_INTERVAL_SECONDS, max(0, deadline_at - time.monotonic())))

        # 循环自然结束（到达截止时间）：按"看到过什么"给出准确原因，不笼统报超时。
        if result.outcome == QrOutcome.FAILED and not result.message:
            if challenge_seen:
                result.outcome = QrOutcome.VERIFICATION_REQUIRED
                result.message = VERIFICATION_MESSAGE
            elif qr_ever_seen:
                result.outcome = QrOutcome.EXPIRED
                result.message = "登录二维码已失效，请重新发起扫码"
            else:
                result.outcome = QrOutcome.FAILED
                result.message = "未能在登录页上找到登录二维码，页面结构可能已变更"

    except Exception as exc:  # noqa: BLE001 - 流程异常统一降级为失败终态
        logger.error("扫码登录流程异常：%s", exc)
        result.outcome = QrOutcome.FAILED
        result.message = "扫码登录失败，请稍后重试"
    finally:
        # 关闭前先刷新免验证凭据缓存：本次若出现过新令牌（例如用户刚完成人工验证），
        # 下一次扫码就能省掉这次人工步骤；令牌只留在内存里。
        if context is not None and native is None:
            await _refresh_verification_cache(context)
        # 无论成功 / 失败 / 取消 / 过期，都必须关掉浏览器并丢弃临时上下文。
        for closer in (
            getattr(context, "close", None),
            getattr(browser, "close", None),
            getattr(native, "close", None),
            getattr(playwright, "stop", None),
        ):
            if closer is None:
                continue
            try:
                await closer()
            except Exception:  # noqa: BLE001 - 关闭失败不掩盖主结果
                pass
        result.elapsed_seconds = round(time.monotonic() - started, 1)
        # 及时丢弃内存中的二维码图片，避免长期驻留。
        if result.outcome != QrOutcome.CONFIRMED:
            result.qr_png_b64 = None
    return result


__all__ = [
    "DEFAULT_QR_LOGIN_URL",
    "QR_SELECTORS",
    "QrBrowserResult",
    "has_verification_cookie",
    "remember_verification_cookies",
    "reset_verification_cache_for_test",
    "run_qr_login",
]
