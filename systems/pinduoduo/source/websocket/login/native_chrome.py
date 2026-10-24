"""系统直接启动 Chrome，再通过本机 CDP 连接。

不更改 User-Agent、webdriver 或页面脚本。每次使用独立临时配置目录；仅打开
about:blank。平台导航须由另外获授权的调用方完成，本模块本身不访问平台。
"""
import asyncio
import errno
import os
import signal
import socket
import tempfile
from pathlib import Path


class NativeChrome:
    def __init__(self):
        self.process = None
        self.browser = None
        self._profile = None
        self._port = None
        # 最近一次 close() 是否确认主进程已退出；供调用方做严格的交接前检查。
        self.exited = False

    async def start(self, chromium, executable, *, timeout=15):
        """chromium 仅用于 connect_over_cdp，绝不调用 launch。"""
        if self.process is not None or self._profile is not None:
            raise RuntimeError('浏览器实例已启动')
        executable = str(Path(executable).resolve(strict=True))
        self._profile = tempfile.TemporaryDirectory(prefix='pdd-native-chrome-')
        self.exited = False
        # 非零临时端口，仅本机监听；不使用 --remote-debugging-port=0。
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
        self._port = port
        args = [executable, '--user-data-dir='+self._profile.name,
                '--remote-debugging-address=127.0.0.1',
                '--remote-debugging-port='+str(port),
                '--no-first-run', '--no-default-browser-check',
                '--lang=zh-CN', '--window-size=1440,900', 'about:blank']
        try:
            self.process = await asyncio.create_subprocess_exec(
                *args, env=dict(os.environ, TZ='Asia/Shanghai'),
                start_new_session=True,
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            deadline = asyncio.get_running_loop().time() + timeout
            # 连接错误只重试本机，不打开网页，也不回退到自动化启动器。
            while asyncio.get_running_loop().time() < deadline:
                if self.process.returncode is not None:
                    raise RuntimeError('独立 Chrome 启动失败，请检查显示和沙箱配置')
                try:
                    self.browser = await chromium.connect_over_cdp(
                        'http://127.0.0.1:'+str(port), timeout=500)
                    return self.browser
                except Exception:
                    await asyncio.sleep(.1)
            raise RuntimeError('独立 Chrome 本机调试连接超时')
        except BaseException:
            await self.close()
            raise

    def _kill_group(self, sig):
        """只对当前仍存活的主进程发信号；已回收的 pid 绝不再碰（避免复用误伤）。"""
        if self.process is None or self.process.returncode is not None:
            return
        try:
            os.killpg(self.process.pid, sig)
        except (ProcessLookupError, PermissionError):
            # 进程组已消失，或已不属于本进程；两种都不应中断收尾。
            pass

    async def close(self):
        """关闭浏览器并确认主进程退出；返回是否确认退出。

        CDP 关闭后若主进程仍存活，立即发 SIGTERM 并等待退出；不先空等一秒。
        发信号前仍检查 returncode，等待失败才升级 SIGKILL。
        """
        try:
            if self.browser is not None:
                try:
                    await asyncio.wait_for(self.browser.close(), timeout=3)
                except Exception:
                    pass
                self.browser = None
            if self.process is not None:
                if self.process.returncode is None:
                    self._kill_group(signal.SIGTERM)
                    try:
                        await asyncio.wait_for(self.process.wait(), timeout=3)
                    except asyncio.TimeoutError:
                        self._kill_group(signal.SIGKILL)
                        try:
                            await asyncio.wait_for(self.process.wait(), timeout=3)
                        except asyncio.TimeoutError:
                            pass
                self.exited = self.process.returncode is not None
                if self.exited:
                    self.process = None
        finally:
            # 未退出的进程可能继续使用目录；保留句柄和目录，允许外层重试清理。
            if self._profile is not None and self.process is None:
                for attempt in range(20):
                    try:
                        self._profile.cleanup()
                        self._profile = None
                        break
                    except OSError as error:
                        if error.errno not in {errno.ENOTEMPTY, errno.EBUSY} or attempt == 19:
                            raise
                        await asyncio.sleep(.1)
        return self.exited

    async def confirm_exited(self, timeout=2.0):
        """在交接前确认浏览器确实退出：主进程已结束且调试端口不可再连。"""
        if not self.exited:
            return False
        if self._port is None:
            return True
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection('127.0.0.1', self._port), timeout=timeout)
        except ConnectionRefusedError:
            return True
        except (OSError, asyncio.TimeoutError):
            return False
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        return False
