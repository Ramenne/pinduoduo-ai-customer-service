"""短信验证映射：仅转发本人提供的验证码，沿用已授权会话的官方表单。"""
import re
import time
from urllib.parse import urlsplit

from login import qr_view

SMS_INPUT = 'input[placeholder*="短信验证码"]:visible'
RESEND_TEXT = re.compile(r'^(获取验证码|重新发送|重新获取|重发验证码|重新发送验证码|获取短信验证码)$')
_LAST_RESEND = {}
SMS_PROBE = r"""() => {
  const visible = e => !!(e && e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden');
  if (Array.from(document.querySelectorAll('.captcha-wrapper,.captcha-wrapper__slider,.slider-img-bg,.slider-item')).some(visible))
    return {type: 'captcha'};
  const fields = Array.from(document.querySelectorAll('input')).filter(e => visible(e) && (e.placeholder || '').includes('短信验证码'));
  if (fields.length !== 1) return null;
  const text = document.body ? document.body.innerText : '';
  const maskedValues = Array.from(document.querySelectorAll('input[disabled]')).map(e => e.value || '').join(' ');
  const phone = (text + ' ' + maskedValues).match(/1\d{2}\*{3,}\d{4}/);
  const countdown = text.match(/(?:还剩|重新发送\s*\(?|重新获取\s*\(?)(\d{1,3})\s*秒/);
  const button = Array.from(document.querySelectorAll('button,[role="button"],a')).find(e =>
    visible(e) && /^(获取验证码|重新发送|重新获取|重发验证码|重新发送验证码|获取短信验证码)$/.test(e.innerText.trim()));
  return {type: 'sms', masked_mobile: phone ? phone[0] : null,
    cooldown_seconds: countdown ? Number(countdown[1]) : 0,
    can_resend: !!(button && !button.disabled && button.getAttribute('aria-disabled') !== 'true' && !countdown),
    invalid_code: /手机验证码错误|短信验证码错误|验证码不正确|验证码已过期|验证码已失效/.test(text)};
}"""


def forget(session_id):
    _LAST_RESEND.pop(session_id, None)


async def inspect(page):
    """只返回预定义的公开字段，不返回页面文本、完整手机号或输入值。"""
    value = await page.evaluate(SMS_PROBE)
    if not isinstance(value, dict):
        return None
    if value.get('type') == 'captcha':
        return {'type': 'captcha'}
    if value.get('type') != 'sms':
        return None
    phone = value.get('masked_mobile')
    if not isinstance(phone, str) or not re.fullmatch(r'1\d{2}\*{3,8}\d{4}', phone):
        phone = None
    seconds = value.get('cooldown_seconds')
    seconds = min(300, max(0, seconds)) if type(seconds) is int else 0
    return {'type': 'sms', 'masked_mobile': phone, 'cooldown_seconds': seconds,
            'can_resend': value.get('can_resend') is True and seconds == 0,
            'error': '验证码错误或已过期，请重新输入' if value.get('invalid_code') is True else ''}


async def perform(session_id, owner, action, code=None):
    if action not in {'submit_sms', 'resend_sms'}:
        return False, '不支持的验证操作'
    if action == 'submit_sms' and (not isinstance(code, str) or not re.fullmatch(r'[0-9]{4,8}', code)):
        return False, '请输入 4～8 位数字验证码'
    page = await qr_view._allowed(session_id, owner)
    lock = qr_view._LOCKS.get(session_id)
    if page is None or lock is None:
        return False, '登录会话已结束，请重新登录'
    async with lock:
        url = urlsplit(page.url)
        if (await qr_view._allowed(session_id, owner) is not page or not qr_view._login_page(page)
                or not url.path.startswith('/login')):
            return False, '当前登录窗口不可操作'
        snapshot = await inspect(page)
        if not snapshot or snapshot['type'] != 'sms':
            return False, '当前没有可填写的短信验证，请等待状态更新'
        if action == 'resend_sms':
            if not snapshot['can_resend'] or time.monotonic()-_LAST_RESEND.get(session_id, -100) < 2:
                return False, '请等待倒计时结束后重新发送'
            target = page.get_by_text(RESEND_TEXT).filter(visible=True)
            if await target.count() != 1 or not await target.is_enabled():
                return False, '暂未找到可用的重新发送按钮'
            if await qr_view._allowed(session_id, owner) is not page:
                return False, '登录会话已结束'
            _LAST_RESEND[session_id] = time.monotonic()
            await target.click(timeout=3000)
            return True, '已请求重新发送，请留意手机短信'
        field = page.locator(SMS_INPUT)
        confirm = page.get_by_role('button', name='确认', exact=True).filter(visible=True)
        if await field.count() != 1 or await confirm.count() != 1:
            return False, '验证页面已变化，请等待状态更新或打开登录窗口'
        await field.fill(code, timeout=3000)
        if await qr_view._allowed(session_id, owner) is not page:
            return False, '登录会话已结束'
        await confirm.click(timeout=3000)
        return True, '验证码已提交，正在等待平台验证结果'
