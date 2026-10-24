"""空白浏览器预启动：全部用替身，不启动浏览器或访问平台。"""
import asyncio
from types import SimpleNamespace
from login.qr_warm_browser import WarmBrowserPool, BlankBrowser


class Lease:
    def __init__(self, pristine=True):
        self.clean = pristine
        self.closed = 0

    async def pristine(self):
        return self.clean

    async def close(self):
        self.closed += 1


def test_take_is_exclusive_and_used_browser_is_never_recycled():
    async def scenario():
        leases = []
        async def factory():
            lease = Lease()
            leases.append(lease)
            return lease
        pool = WarmBrowserPool(factory)
        pool.start()
        await pool.task
        first, second = await asyncio.gather(pool.take(), pool.take())
        assert first is leases[0] and second is None
        assert pool.slot is None
        # 只有用完并销毁后才补充新的资源，不把用过的实例放回。
        await first.close()
        pool.replenish()
        await pool.task
        assert pool.slot is leases[1] and pool.slot is not first
        await pool.stop()
        assert all(lease.closed == 1 for lease in leases)
    asyncio.run(scenario())


def test_start_is_nonblocking_and_failure_allows_cold_fallback():
    async def scenario():
        async def failure():
            raise RuntimeError('fixture')
        pool = WarmBrowserPool(failure)
        pool.start()
        assert await pool.take() is None
        await pool.task
        assert await pool.take() is None
        await pool.stop()
    asyncio.run(scenario())


def test_stop_cancels_inflight_preparation_and_does_not_refill():
    async def scenario():
        started, cleaned = asyncio.Event(), asyncio.Event()
        async def factory():
            try:
                started.set()
                await asyncio.Event().wait()
            finally:
                cleaned.set()
        pool = WarmBrowserPool(factory)
        pool.start()
        await started.wait()
        await pool.stop()
        assert cleaned.is_set() and pool.slot is None
        pool.replenish()
        assert pool.task is None
    asyncio.run(scenario())


def test_invalid_or_cancelled_lease_is_destroyed():
    async def scenario():
        pool = WarmBrowserPool()
        lease = Lease(False)
        pool.slot = lease
        assert await pool.take() is None
        assert lease.closed == 1
        entered = asyncio.Event()
        async def pending():
            entered.set()
            await asyncio.Event().wait()
        lease = Lease()
        lease.pristine = pending
        pool.slot = lease
        task = asyncio.create_task(pool.take())
        await entered.wait()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert lease.closed == 1 and pool.slot is None
    asyncio.run(scenario())


def test_pristine_rejects_cookies_nonblank_page_and_dead_process():
    async def scenario():
        class Context:
            pages = [SimpleNamespace(url='about:blank')]
            items = []
            async def cookies(self): return self.items
        context = Context()
        browser = SimpleNamespace(contexts=[context], is_connected=lambda: True)
        native = SimpleNamespace(process=SimpleNamespace(returncode=None))
        lease = BlankBrowser(None, native, browser)
        assert await lease.pristine()
        context.items = [{'name': 'any_cookie'}]
        assert not await lease.pristine()
        context.items = []
        context.pages = [SimpleNamespace(url='https://example.test/')]
        assert not await lease.pristine()
        context.pages = [SimpleNamespace(url='about:blank')]
        native.process.returncode = 0
        assert not await lease.pristine()
    asyncio.run(scenario())
