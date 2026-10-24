"""人工登录窗口：只转发用户实际输入，不生成验证答案。"""
import asyncio
import base64
import math
from urllib.parse import urlsplit

_PAGES = {}
_LOCKS = {}
_KEYS = {"Tab", "Enter", "Space", "Backspace", "Escape", "ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Delete", "Home", "End", "Control+A"}


async def attach(session_id, page):
    _PAGES[session_id] = page
    _LOCKS[session_id] = asyncio.Lock()


async def detach(session_id):
    from login.password_verification import forget
    forget(session_id)
    _PAGES.pop(session_id, None)
    _LOCKS.pop(session_id, None)


async def _allowed(session_id, owner):
    from login import qr_session_store
    view = await qr_session_store.get_session(session_id, owner)
    if not view or view.get("terminal") or view.get("state") not in {"waiting", "scanned", "verification_required"}:
        return None
    session = qr_session_store._SESSIONS.get(session_id)
    # 无过期时间的会话不得开放交互窗口（失败即关闭，避免无限期可操作）。
    if session is None or session.expires_at is None or session.expires_at <= qr_session_store.now_beijing_naive():
        return None
    return _PAGES.get(session_id)


def _login_page(page):
    url = urlsplit(page.url)
    return url.scheme == "https" and url.hostname == "mms.pinduoduo.com"


async def _viewport(page):
    # native CDP 上下文通常没有 viewport_size；必须读真实页面尺寸，避免点击纵坐标偏移。
    size = page.viewport_size
    if size is None:
        size = await page.evaluate("() => ({width: window.innerWidth, height: window.innerHeight})")
    if (not isinstance(size, dict) or any(isinstance(size.get(k), bool)
            or not isinstance(size.get(k), (int, float)) or not math.isfinite(size[k])
            or not 1 <= size[k] <= 16384 for k in ('width', 'height'))):
        raise ValueError('窗口尺寸不可用')
    return size


async def frame(session_id, owner):
    page = await _allowed(session_id, owner)
    lock = _LOCKS.get(session_id)
    if page is None or lock is None:
        return None
    async with lock:
        if await _allowed(session_id, owner) is not page or not _login_page(page):
            return None
        jpeg = await page.screenshot(type="jpeg", quality=90, timeout=5000)
        viewport = await _viewport(page)
        return {"image": base64.b64encode(jpeg).decode(), **viewport}


async def input_event(session_id, owner, event):
    page = await _allowed(session_id, owner)
    lock = _LOCKS.get(session_id)
    if page is None or lock is None:
        return False
    kind = event.get("kind")
    if kind not in {"move", "down", "up", "key", "wheel", "text"}:
        return False
    if kind == "wheel" and any(
        isinstance(event.get(k), bool) or not isinstance(event.get(k), (int, float))
        or not math.isfinite(event[k]) or abs(event[k]) > 2000
        for k in ("delta_x", "delta_y")
    ):
        return False
    if kind == "text":
        text = event.get("text")
        if not isinstance(text, str) or not 1 <= len(text) <= 256 or any(ord(c) < 32 for c in text):
            return False
    if kind == "key":
        if event.get("key") not in _KEYS:
            return False
    elif kind != "text" and any(isinstance(event.get(k), bool) or not isinstance(event.get(k), (int,float)) or not math.isfinite(event[k]) or not 0 <= event[k] <= 1 for k in ("x","y")):
        return False
    async with lock:
        if await _allowed(session_id, owner) is not page or not _login_page(page):
            return False
        if kind == "key":
            await page.keyboard.press(event["key"])
        elif kind == "text":
            await page.keyboard.insert_text(event["text"])
        else:
            viewport = await _viewport(page)
            await page.mouse.move(event["x"]*(viewport["width"]-1), event["y"]*(viewport["height"]-1))
            if kind == "wheel":
                await page.mouse.wheel(event["delta_x"], event["delta_y"])
            elif kind == "down":
                await page.mouse.down()
            elif kind == "up":
                await page.mouse.up()
        return True
