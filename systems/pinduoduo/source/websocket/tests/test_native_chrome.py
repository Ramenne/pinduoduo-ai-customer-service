# -*- coding: utf-8 -*-
"""NativeChrome 安全退出契约：不启动真实浏览器、不发网络请求。"""
import asyncio
import signal

from login import native_chrome as nc_module
from login.native_chrome import NativeChrome


class _ReapedProcess:
    """已结束、pid 可能已被系统复用的进程替身。"""

    pid = 424242
    returncode = 0


def test_kill_group_never_signals_a_reaped_process(monkeypatch):
    """主进程已回收后绝不能补发信号，否则可能误伤被复用的 pid。"""
    chrome = NativeChrome()
    chrome.process = _ReapedProcess()
    signalled = []
    monkeypatch.setattr(nc_module.os, "killpg",
                        lambda pid, sig: signalled.append((pid, sig)))
    chrome._kill_group(signal.SIGTERM)
    chrome._kill_group(signal.SIGKILL)
    assert signalled == []


def test_kill_group_without_process_is_a_noop(monkeypatch):
    chrome = NativeChrome()
    signalled = []
    monkeypatch.setattr(nc_module.os, "killpg",
                        lambda pid, sig: signalled.append((pid, sig)))
    chrome._kill_group(signal.SIGTERM)
    assert signalled == []


def test_confirm_exited_requires_process_and_closed_debug_port():
    """交接前必须同时确认主进程已退出且调试端口不可再连。"""
    async def scenario():
        chrome = NativeChrome()
        assert await chrome.confirm_exited(timeout=0.2) is False

        chrome.exited = True
        chrome._port = None
        assert await chrome.confirm_exited(timeout=0.2) is True

        server = await asyncio.start_server(lambda r, w: w.close(), "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        chrome._port = port
        assert await chrome.confirm_exited(timeout=0.5) is False

        server.close()
        await server.wait_closed()
        assert await chrome.confirm_exited(timeout=0.5) is True

    asyncio.run(scenario())


def test_close_without_process_reports_last_known_exit_state():
    """重复 close() 是幂等的；不会把已确认的退出状态改回 False。"""
    async def scenario():
        chrome = NativeChrome()
        assert await chrome.close() is False
        chrome.exited = True
        assert await chrome.close() is True

    asyncio.run(scenario())


def test_debug_port_timeout_does_not_prove_exit(monkeypatch):
    async def timeout(*_args, **_kwargs):
        raise asyncio.TimeoutError()
    monkeypatch.setattr(nc_module.asyncio, "open_connection", timeout)
    async def scenario():
        chrome = NativeChrome()
        chrome.exited = True
        chrome._port = 12345
        assert await chrome.confirm_exited() is False
    asyncio.run(scenario())


def test_close_terminates_live_process_before_waiting(monkeypatch):
    events = []
    class Process:
        pid = 424242
        returncode = None
        async def wait(self):
            assert events == [signal.SIGTERM]
            events.append("wait")
            self.returncode = 0
    def killpg(_pid, sig):
        events.append(sig)
    monkeypatch.setattr(nc_module.os, "killpg", killpg)
    async def scenario():
        chrome = NativeChrome()
        chrome.process = Process()
        assert await chrome.close()
        assert chrome.process is None
        assert events == [signal.SIGTERM, "wait"]
        assert await chrome.close()
        assert events == [signal.SIGTERM, "wait"]
    asyncio.run(scenario())


def test_close_never_signals_process_already_reaped_during_cdp_close(monkeypatch):
    events = []
    process = _ReapedProcess()
    class Browser:
        async def close(self):
            events.append("cdp_close")
    monkeypatch.setattr(nc_module.os, "killpg", lambda *_: events.append("unexpected_signal"))
    async def scenario():
        chrome = NativeChrome()
        chrome.process = process
        chrome.browser = Browser()
        assert await chrome.close()
        assert events == ["cdp_close"]
    asyncio.run(scenario())
