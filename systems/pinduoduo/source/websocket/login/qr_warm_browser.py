"""只预启动独立空白 Chrome；领取即移出，使用后永不归还。

空闲实例不导航到平台、不装入 Cookie、不持有任何用户会话。每次补充都创建
新的临时目录和驱动；准备失败时扫码入口仍可按原方式冷启动。
"""
import asyncio
import logging
import time
from dataclasses import dataclass

from playwright.async_api import async_playwright
from common.core.config import get_settings
from login.native_chrome import NativeChrome

logger = logging.getLogger(__name__)


@dataclass
class BlankBrowser:
    playwright: object
    native: NativeChrome
    browser: object

    async def pristine(self):
        if (self.native.process is None or self.native.process.returncode is not None
                or not self.browser.is_connected()):
            return False
        for context in self.browser.contexts:
            if any(page.url != 'about:blank' for page in context.pages):
                return False
            if await context.cookies():
                return False
        return bool(self.browser.contexts)

    async def close(self):
        try:
            await self.native.close()
        finally:
            await self.playwright.stop()


async def _prepare_blank():
    playwright = await async_playwright().start()
    native = NativeChrome()
    try:
        browser = await native.start(playwright.chromium, playwright.chromium.executable_path)
        lease = BlankBrowser(playwright, native, browser)
        if not await lease.pristine():
            raise RuntimeError('预启动实例不是独立空白浏览器')
        return lease
    except BaseException:
        try:
            await native.close()
        finally:
            await playwright.stop()
        raise


class WarmBrowserPool:
    """同一个事件循环内原子移交唯一空闲实例；关闭服务时回收待领取资源。"""
    def __init__(self, factory=None):
        self.factory = factory or _prepare_blank
        self.slot = None
        self.task = None
        self.closed = True

    def start(self):
        self.closed = False
        self.replenish()

    def replenish(self):
        if (not self.closed and self.slot is None
                and (self.task is None or self.task.done())):
            self.task = asyncio.create_task(self._build())

    async def _build(self):
        lease = None
        started = time.monotonic()
        try:
            lease = await self.factory()
            if not self.closed:
                self.slot, lease = lease, None
                logger.info('预启动空白扫码浏览器就绪 elapsed_ms=%s',
                            round((time.monotonic() - started) * 1000))
        except Exception as error:
            logger.warning('预启动失败，扫码可回退冷启动 type=%s', type(error).__name__)
        finally:
            if lease is not None:
                await lease.close()

    async def take(self):
        # 移出发生在首次 await 前，另一个用户永远拿不到同一实例。
        lease, self.slot = self.slot, None
        if lease is None:
            return None
        try:
            if await asyncio.wait_for(lease.pristine(), timeout=1):
                logger.info('扫码已领取独立预启动浏览器')
                return lease
        except asyncio.CancelledError:
            await lease.close()
            raise
        except Exception:
            pass
        await lease.close()
        return None

    async def stop(self):
        self.closed = True
        if self.task is not None and not self.task.done():
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        lease, self.slot = self.slot, None
        if lease is not None:
            await lease.close()
        self.task = None


_POOL = WarmBrowserPool()


def _enabled():
    settings = get_settings()
    return (getattr(settings, 'qr_login_prewarm', False)
            and getattr(settings, 'qr_login_transport', '') == 'hybrid'
            and getattr(settings, 'qr_login_browser_launcher', '') == 'native')


def start_warm_browser():
    if _enabled():
        _POOL.start()


async def stop_warm_browser():
    await _POOL.stop()


async def borrow_warm_browser():
    return await _POOL.take() if _enabled() else None


def replenish_warm_browser():
    if _enabled():
        _POOL.replenish()
