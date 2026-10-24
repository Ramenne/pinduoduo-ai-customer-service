"""账号密码会话：独立可见 Chrome，用户操作验证，Cookie 与接口共同核验。

不根据标题猜测成功，不记录账号密码或页面文本；表单变化时开放人工窗口。
"""
import asyncio
import logging
import time
from urllib.parse import urlsplit

from playwright.async_api import async_playwright
from login.native_chrome import NativeChrome
from login.password_verification import inspect as inspect_verification
from login.qr_warm_browser import borrow_warm_browser, replenish_warm_browser
from login.qr_hybrid import _close_browser, _interrupted, _thread_call, _sleep_or_cancel
from login.qr_state import MMS_COOKIES_URL, PASS_ID_COOKIE, QrBrowserResult, QrOutcome, classify_page
from channel_pdd.pdd_login import extract_login_identity

logger = logging.getLogger(__name__)
LOGIN_URL = 'https://mms.pinduoduo.com/login/'
_ERROR_PROBE = """() => {
  const text = document.body ? document.body.innerText : '';
  return {
    badPassword: /账号或密码错误|用户名或密码错误|密码不正确|密码错误|账号不存在|账户不存在/.test(text),
    locked: /账号被锁定|账号已锁定|登录次数过多|尝试次数过多/.test(text)
  };
}"""


class AuthDiagnostics:
    """只保留官方认证接口的安全枚举字段，响应正文及个人信息不留存。"""
    def __init__(self):
        self.error_code = None
        self.category = None

    async def __call__(self, response):
        url = urlsplit(response.url)
        if url.scheme != 'https' or url.hostname != 'mms.pinduoduo.com' or url.path != '/janus/api/auth':
            return
        try:
            body = await response.json()
        except Exception:
            return
        code = body.get('errorCode') if isinstance(body, dict) else None
        if isinstance(code, bool) or not isinstance(code, int):
            return
        self.error_code = code
        self.category = {2000015: 'phone_verification', 2000020: 'phone_verification',
                         2000021: 'subaccount_phone_verification',
                         2000027: 'phone_verification', 2000017: 'sms_code_error'}.get(code)
        logger.info('账号认证响应 error_code=%s category=%s', code, self.category or 'other')


async def _fill_and_submit(page, username, password, timeout_ms):
    # 用可见输入框和文字定位，避免依赖构建生成的 CSS 类名。
    password_box = page.locator('input[type="password"]:visible').first
    if not await password_box.is_visible():
        await page.get_by_text('账号登录', exact=True).first.click(timeout=timeout_ms)
    await password_box.wait_for(state='visible', timeout=timeout_ms)
    username_box = page.locator(
        'input[placeholder*="账号"]:visible, input[placeholder*="手机"]:visible, '
        'input[type="text"]:visible, input[type="tel"]:visible').first
    await username_box.fill(username, timeout=timeout_ms)
    await password_box.fill(password, timeout=timeout_ms)
    await page.get_by_role('button', name='登录', exact=True).first.click(timeout=timeout_ms)


async def run_password_session(*, username, password, deadline_at, cancel_event,
                               on_state_change=None, on_page_ready=None,
                               expected_shop_id=None, on_verification_change=None, **_kwargs):
    result = QrBrowserResult()
    playwright = native = browser = context = None
    last_state = None
    async def status(state, message):
        nonlocal last_state
        if (state, message) != last_state and on_state_change:
            await on_state_change(state, message)
        last_state = (state, message)
    try:
        if _interrupted(result, cancel_event, deadline_at):
            return result
        await status(QrOutcome.WAITING, '正在启动独立登录浏览器')
        warm = await borrow_warm_browser()
        if warm:
            playwright, native, browser = warm.playwright, warm.native, warm.browser
        else:
            playwright = await async_playwright().start()
            native = NativeChrome()
            browser = await native.start(playwright.chromium, playwright.chromium.executable_path)
        context = browser.contexts[0]
        page = await context.new_page()
        diagnostics = AuthDiagnostics()
        page.on('response', diagnostics)
        if on_page_ready:
            await on_page_ready(page)
        await status(QrOutcome.WAITING, '正在打开账号登录页')
        remaining = max(1, int((deadline_at-time.monotonic())*1000))
        await page.goto(LOGIN_URL, wait_until='domcontentloaded', timeout=min(30000, remaining))
        # 全新实例不得带入已有登录态，防止用旧身份解释本次输入。
        if any(c.get('name') == PASS_ID_COOKIE for c in await context.cookies(MMS_COOKIES_URL)):
            result.outcome, result.message = QrOutcome.FAILED, '登录浏览器含已有登录态，已停止本次登录'
            return result
        if _interrupted(result, cancel_event, deadline_at):
            return result
        await status(QrOutcome.WAITING, '正在填写账号并提交登录')
        try:
            await _fill_and_submit(page, username, password,
                                   max(1, min(5000, int((deadline_at-time.monotonic())*1000))))
            await status(QrOutcome.WAITING, '已提交，正在等待平台登录结果')
        except Exception:
            await status(QrOutcome.VERIFICATION_REQUIRED,
                         '未能自动完成表单操作，请打开登录窗口，处理弹窗或由本人完成登录')
        # 不再保留供后续代码使用的明文密码。
        password = ''
        identity_attempts = 0
        while not _interrupted(result, cancel_event, deadline_at):
            items = await context.cookies(MMS_COOKIES_URL)
            cookies = {c['name']: c['value'] for c in items}
            if cookies.get(PASS_ID_COOKIE):
                await status(QrOutcome.WAITING, '已收到登录凭据，正在核验店铺身份')
                identity = await _thread_call(extract_login_identity, cookies, cancel_event=cancel_event)
                if _interrupted(result, cancel_event, deadline_at):
                    return result
                if identity and identity.get('shop_id') and identity.get('user_id'):
                    if expected_shop_id and str(identity['shop_id']) != str(expected_shop_id):
                        result.outcome, result.message = QrOutcome.FAILED, '登录店铺与目标店铺不一致，未保存'
                        return result
                    # 核验完也必须关闭浏览器；关闭失败不能交付凭据。
                    if not await _close_browser(native, browser, context):
                        result.outcome, result.message = QrOutcome.FAILED, '无法确认浏览器退出，未保存登录结果'
                        return result
                    if _interrupted(result, cancel_event, deadline_at):
                        return result
                    result.outcome, result.message = QrOutcome.CONFIRMED, '账号密码登录成功，店铺身份已核验'
                    result.cookies, result.identity = cookies, identity
                    return result
                identity_attempts += 1
                if identity_attempts >= 3:
                    result.outcome, result.message = QrOutcome.FAILED, '已收到登录凭据，但店铺身份核验未通过，未保存'
                    return result
            else:
                try:
                    verification = await inspect_verification(page)
                except Exception:
                    verification = None
                if on_verification_change:
                    await on_verification_change(verification)
                probe = await classify_page(page)
                if verification and verification.get('type') == 'captcha':
                    await status(QrOutcome.VERIFICATION_REQUIRED, '平台要求人工图形验证，请打开验证窗口完成')
                elif diagnostics.category in {'phone_verification', 'subaccount_phone_verification'}:
                    await status(QrOutcome.VERIFICATION_REQUIRED, '平台要求验证绑定手机，请在下方填写短信验证码后确认')
                elif diagnostics.category == 'sms_code_error':
                    await status(QrOutcome.VERIFICATION_REQUIRED, '平台提示短信验证码错误，请重新输入')
                elif probe.needs_verification:
                    await status(QrOutcome.VERIFICATION_REQUIRED, '平台要求人工安全验证，请打开登录窗口由本人完成')
                try:
                    error = await page.evaluate(_ERROR_PROBE)
                except Exception:
                    error = {}
                if error.get('locked') or error.get('badPassword'):
                    result.outcome = QrOutcome.FAILED
                    result.message = ('平台提示登录次数受限，请稍后再试' if error.get('locked')
                                      else '平台提示账号或密码错误，请检查后重新提交')
                    return result
            await _sleep_or_cancel(cancel_event, .5)
        if result.outcome == QrOutcome.EXPIRED:
            result.message = ('人工验证尚未完成，本次登录已超时' if last_state and last_state[0] == QrOutcome.VERIFICATION_REQUIRED
                              else '等待登录结果超时，尚未确认登录成功；超时不代表密码错误')
        return result
    except Exception as error:
        logger.warning('账号密码会话异常 type=%s', type(error).__name__)
        if not _interrupted(result, cancel_event, deadline_at):
            result.outcome, result.message = QrOutcome.FAILED, '登录页面或服务异常，请取消后重试'
        return result
    finally:
        password = ''
        try:
            if native:
                await native.close()
        finally:
            try:
                if playwright:
                    await playwright.stop()
            finally:
                if on_page_ready:
                    await on_page_ready(None)
                replenish_warm_browser()
