"""从数据库授权重建隔离浏览器上下文；不依赖旧用户名浏览器目录。"""
from urllib.parse import urlsplit
from playwright.async_api import async_playwright

HOME = 'https://mms.pinduoduo.com/home/'


async def refresh_from_cookies(cookies):
    if not cookies:
        return {'state': 'expired'}
    async with async_playwright() as driver:
        browser = await driver.chromium.launch(headless=True,
            args=['--no-sandbox', '--disable-dev-shm-usage'])
        try:
            context = await browser.new_context(locale='zh-CN', timezone_id='Asia/Shanghai')
            await context.add_cookies([{'name': str(k), 'value': str(v),
                'domain': '.pinduoduo.com', 'path': '/', 'secure': True}
                for k, v in cookies.items() if isinstance(v, (str, int, float))])
            page = await context.new_page()
            response = await page.goto(HOME, wait_until='domcontentloaded', timeout=20000)
            await page.wait_for_timeout(1500)  # 页面完成自身的授权/续期脚本。
            current = urlsplit(page.url)
            if current.hostname not in ('mms.pinduoduo.com', 'mms.pinduoduo.com.cn'):
                return {'state': 'temporary_error', 'reason': 'unexpected_destination'}
            if 'login' in current.path.lower():
                return {'state': 'expired', 'reason': 'login_required'}
            if response is not None and response.status >= 400:
                return {'state': 'temporary_error', 'reason': 'page_unavailable'}
            # 只读取平台可见验证提示；不点击、不提交验证码或账号密码。
            if await page.get_by_text('安全验证', exact=True).count() or await page.get_by_text('请完成验证', exact=True).count():
                return {'state': 'verification_required'}
            items = await context.cookies('https://mms.pinduoduo.com/')
            return {'state': 'candidate', 'cookies': {c['name']: c['value'] for c in items}}
        finally:
            await browser.close()
