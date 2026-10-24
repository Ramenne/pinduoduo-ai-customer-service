# -*- coding: utf-8 -*-
"""
login.qr_session_store —— 扫码登录会话注册表（进程内）
=====================================================
本文件用途：为 websocket 服务保存「扫码登录会话」的运行时状态，供 backend 经
服务间 HTTP 轮询查询。每个会话背后对应一个一次性的 Playwright 浏览器流程
（见 ``login.qr_browser``）。

为什么用进程内字典而不是数据库 / Redis：
- 扫码会话是**秒级到分钟级的临时态**，落库会带来无意义的表与清理任务；
- 项目既有惯例即如此 —— ``channel_pdd.connection_registry`` 用模块级字典按
  ``(owner_user_id, shop_id)`` 保存长连接状态，本模块沿用同一口径（规范 36）；
- 本仓库**没有 Redis 客户端封装**（``common/core/config.py`` 只有连接串配置，
  全仓无 ``import redis``），不为这一个功能引入新依赖。
- 代价：websocket 服务重启会丢弃进行中的扫码会话。前端会因此拿到「会话不存在」，
  提示重新发起即可，不影响任何已保存的凭据。

安全约束（对应本轮实现要求）：
- 会话创建即绑定 ``owner_user_id``；查询 / 取消 / 领取都**必须**归属一致，
  否则一律按「会话不存在」处理，不区分「存在但无权」以免泄露他人会话存在性。
- 管理员 **不** 旁路：扫码会话是发起者本人的临时资源。
- 二维码图片与登录凭据只驻留内存：不落盘、不写日志、不进普通日志文件。
- 凭据只能被「领取（claim）」一次：领取成功后状态转 ``claimed`` 并立即丢弃
  Cookie，重复领取返回「已领取」，从而在结构上杜绝重复绑定与凭据外泄。
- 进入终态即释放浏览器资源并清空图片；过期会话由清扫回收。

实现约束（开发规范）：导入置顶（51）、中文注释（37）、文件名用下划线（40）、
单文件 ≤500 行（35）、日志禁用 debug（38）、并发与超时经环境变量（21）、
时间统一北京时间（17）。
"""
from __future__ import annotations

import asyncio
import logging
import os
import secrets
import time
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Dict, Optional, Tuple

from common.core.config import get_settings
from common.utils.time_utils import now_beijing_naive, safe_isoformat
from login.qr_state import QrBrowserResult, QrOutcome

logger = logging.getLogger("login.qr_session_store")

# 终态之后会话在内存中保留的宽限秒数：让前端至少能取到一次最终状态。
_TTL_AFTER_TERMINAL_SECONDS: int = 180

# 清扫阈值：超过该数量时主动回收已过期的非终态会话。
_SWEEP_THRESHOLD: int = 50

# 本进程 PID。扫码会话只存在于创建它的进程内存中，多 worker 部署时用于识别
# "轮询打到了另一个 worker"这一类故障，给出准确提示而不是含糊的"会话不存在"。
_PROCESS_PID: int = os.getpid()


@dataclass
class QrLoginSession:
    """一次扫码登录会话。"""

    session_id: str
    owner_user_id: int
    target_shop_id: Optional[str]
    login_mode: str = "qr"
    _login_credentials: Optional[dict] = field(default=None, repr=False)
    _password: Optional[str] = field(default=None, repr=False)
    _remark: Optional[str] = field(default=None, repr=False)
    verification: Optional[dict] = None
    state: str = QrOutcome.WAITING
    message: str = ""
    created_at: Any = field(default_factory=now_beijing_naive)
    expires_at: Any = None
    qr_png_b64: Optional[str] = None
    qr_rotation: int = 0
    shop_id: Optional[str] = None
    shop_name: Optional[str] = None
    shop_logo: Optional[str] = None
    # 拼多多登录账号名（与 Cookie 导入路径的 username 口径一致，供落库反显）。
    username: Optional[str] = None
    # 凭据仅在 confirmed -> claimed 之间短暂驻留，领取后立即清空。
    _cookies: Optional[Dict[str, str]] = field(default=None, repr=False)
    task: Optional[Any] = field(default=None, repr=False)
    cancel_event: Optional[Any] = field(default=None, repr=False)
    # 进入终态的单调时钟时刻，用于宽限期回收；None 表示尚未结束。
    _finished_at: Optional[float] = field(default=None, repr=False)

    @property
    def terminal(self) -> bool:
        """是否已进入终态。"""
        return QrOutcome.is_terminal(self.state)

    def public_view(self) -> Dict[str, Any]:
        """对外可见字段（**绝不含 Cookie**）。"""
        return {
            "session_id": self.session_id,
            "login_mode": self.login_mode,
            "verification": self.verification,
            "owner_user_id": self.owner_user_id,
            "target_shop_id": self.target_shop_id,
            "state": self.state,
            "terminal": self.terminal,
            "message": self.message,
            "qr_image_base64": self.qr_png_b64,
            "qr_rotation": self.qr_rotation,
            "shop_id": self.shop_id,
            "shop_name": self.shop_name,
            "created_at": safe_isoformat(self.created_at),
            "expires_at": safe_isoformat(self.expires_at) if self.expires_at else None,
            # 会话只存在于创建它的那个子进程内存里。把 pid 带出去，backend 才能在
            # "创建"与"轮询"落到不同 worker 时给出准确原因，而不是含糊的"不存在"。
            "worker_pid": _PROCESS_PID,
        }


# 模块级会话表与并发信号量（懒加载，避免在导入期绑定事件循环）。
_SESSIONS: Dict[str, QrLoginSession] = {}
_LOCK: Optional[asyncio.Lock] = None
_SEMAPHORE: Optional[asyncio.Semaphore] = None


def _lock() -> asyncio.Lock:
    """获取会话表互斥锁（懒加载）。"""
    global _LOCK
    if _LOCK is None:
        _LOCK = asyncio.Lock()
    return _LOCK


def _semaphore() -> asyncio.Semaphore:
    """获取扫码并发信号量（``QR_LOGIN_MAX_CONCURRENT``，至少 1）。"""
    global _SEMAPHORE
    if _SEMAPHORE is None:
        limit = max(1, int(getattr(get_settings(), "qr_login_max_concurrent", 1) or 1))
        _SEMAPHORE = asyncio.Semaphore(limit)
        logger.info("初始化扫码登录并发信号量，最大并发数=%s", limit)
    return _SEMAPHORE


def _new_session_id() -> str:
    """生成不可猜测的会话标识（密码学随机，避免枚举他人会话）。"""
    return secrets.token_urlsafe(24)


def _sweep_locked() -> int:
    """回收结束的会话，并把"卡住"的会话强制置为终态（调用方须持锁）。

    两类需要兜底的情况：
    - 后台任务已结束却没写回结果（例如任务被取消、进程异常），会话会永远停在
      ``waiting``，前端就会无限轮询；
    - 已过 ``expires_at`` 但仍未终态。
    两者都会被强制落到一个明确的终态并给出中文原因，避免"无声丢会话"。
    """
    now = time.monotonic()
    beijing_now = now_beijing_naive()
    stale = []
    for session_id, session in _SESSIONS.items():
        if session.terminal:
            grace = session._finished_at
            if grace is not None and now - grace > _TTL_AFTER_TERMINAL_SECONDS:
                stale.append(session_id)
            continue
        task_dead = session.task is not None and session.task.done()
        past_expiry = session.expires_at is not None and beijing_now > session.expires_at
        if task_dead or past_expiry:
            if session.cancel_event is not None:
                session.cancel_event.set()
            if session.task is not None and not session.task.done():
                session.task.cancel()
            session.state = QrOutcome.EXPIRED
            session.message = ("账号登录已超时或服务端中断，请重新提交" if session.login_mode == "password"
                               else "扫码会话已超时或服务端已中断，请重新发起扫码")
            session.verification = None
            session.qr_png_b64 = None
            session._cookies = None
            _clear_password(session)
            session._finished_at = now
            logger.warning("会话 %s 未正常收尾，已强制置为 expired", session_id)
    for session_id in stale:
        _discard_locked(session_id)
    if stale:
        logger.info("已回收 %s 个结束的扫码会话", len(stale))
    return len(stale)


def _discard_locked(session_id: str) -> None:
    """丢弃会话并清空其中的图片与凭据（调用方须持锁）。"""
    session = _SESSIONS.pop(session_id, None)
    if session is None:
        return
    session.qr_png_b64 = None
    session._cookies = None
    _clear_password(session)
    task = session.task
    if task is not None and not task.done():
        task.cancel()


def _clear_password(session):
    session._password = None
    if session._login_credentials is not None:
        session._login_credentials.clear()
        session._login_credentials = None


def _apply_result(session: QrLoginSession, result: QrBrowserResult) -> None:
    """把浏览器流程结果落到会话上（调用方须持锁）。"""
    # 用户已取消的会话不得被后台结果"复活"——否则会出现"前端已关闭、会话却又变成
    # 已确认"的错觉，并可能触发一次非用户预期的落库。
    if session.state in {QrOutcome.CANCELLED, QrOutcome.EXPIRED}:
        logger.info("会话 %s 已被用户取消，忽略后台流程结果 %s",
                    session.session_id, result.outcome)
        return

    session.verification = None
    session.state = result.outcome
    session.message = result.message
    if result.qr_png_b64:
        session.qr_png_b64 = result.qr_png_b64
        session.qr_rotation = result.qr_rotation
    if result.outcome == QrOutcome.CONFIRMED:
        identity = result.identity or {}
        session._cookies = result.cookies
        session.shop_id = str(identity.get("shop_id") or "") or None
        session.shop_name = identity.get("shop_name")
        session.shop_logo = identity.get("shop_logo")
        session.username = identity.get("user_name")
    # 进入终态即不再需要二维码图片驻留内存（图片本身也是可被利用的登录入口）。
    if QrOutcome.is_terminal(result.outcome):
        session.qr_png_b64 = None


async def update_display_state(session_id: str, state: str, message: str) -> None:
    """更新**可恢复中间态**（如需要人工验证、已扫码待确认）。

    终态一律由 ``_apply_result`` 决定，本函数对终态直接忽略，避免出现
    "中间态回调把已确认的会话改回等待"这类倒退。
    """
    if QrOutcome.is_terminal(state):
        return
    async with _lock():
        session = _SESSIONS.get(session_id)
        if session is None or session.terminal:
            return
        session.state = state
        session.message = message
        # 未出码时清空图片，避免前端继续展示上一轮的旧码。
        if state == QrOutcome.VERIFICATION_REQUIRED:
            session.qr_png_b64 = None


async def _runner(session_id: str, deadline_at: float) -> None:
    """后台执行一次扫码流程，并把结果写回会话表。"""
    # 惰性导入浏览器层：本模块因此可以在没有 playwright / channel_pdd 的环境里被
    # 导入和单测（与 channel_pdd.core.session_relogin 惰性接入 pdd_login 同一手法）。
    from login.qr_browser import run_qr_login, verification_owner
    from login import qr_view

    cancel_event = asyncio.Event()
    async with _lock():
        session = _SESSIONS.get(session_id)
        if session is None:
            return
        session.cancel_event = cancel_event

    async def _on_qr(png_b64: str, rotation: int) -> None:
        async with _lock():
            current = _SESSIONS.get(session_id)
            if current is not None and not current.terminal:
                current.qr_png_b64 = png_b64
                current.qr_rotation = rotation
                if current.state == QrOutcome.VERIFICATION_REQUIRED:
                    current.state = QrOutcome.WAITING
                    current.message = "安全验证已通过，请扫码登录"

    async def _on_state(state: str, message: str) -> None:
        await update_display_state(session_id, state, message)

    async def _on_verification(data):
        async with _lock():
            current = _SESSIONS.get(session_id)
            if current is not None and not current.terminal:
                current.verification = data

    async def _on_page(page):
        if page is None:
            await qr_view.detach(session_id)
        else:
            await qr_view.attach(session_id, page)

    async with _semaphore():
        owner_token = verification_owner.set(session.owner_user_id)
        credentials = session._login_credentials or {}
        session._login_credentials = None
        runner_options = {}
        try:
            transport = getattr(get_settings(), "qr_login_transport", "browser")
            runner = run_qr_login
            runner_options = {}
            if session.login_mode == "password":
                from login.password_session import run_password_session
                runner = run_password_session
                runner_options.update(credentials)
                runner_options["on_verification_change"] = _on_verification
                runner_options["expected_shop_id"] = session.target_shop_id
            elif transport == "hybrid":
                from login.qr_hybrid import run_qr_hybrid
                runner = run_qr_hybrid
                runner_options["expected_shop_id"] = session.target_shop_id
            elif transport != "browser":
                raise ValueError("不支持的扫码传输方式")
            result = await runner(
                deadline_at=deadline_at,
                cancel_event=cancel_event,
                on_qr_update=_on_qr,
                on_state_change=_on_state,
                on_page_ready=_on_page,
                **runner_options,
            )
            if session.login_mode == "password" and result.outcome == QrOutcome.CONFIRMED:
                result.identity = dict(result.identity or {}, user_name=credentials.get("username"))
                async with _lock():
                    if not session.terminal and not cancel_event.is_set():
                        session._password = credentials.get("password")
        except Exception as exc:  # noqa: BLE001 - 后台任务异常不得冒泡
            logger.error("登录会话 %s 执行异常 type=%s", session_id, type(exc).__name__)
            result = QrBrowserResult(outcome=QrOutcome.FAILED, message="扫码登录失败，请稍后重试")
        finally:
            credentials.clear()
            runner_options.clear()
            verification_owner.reset(owner_token)
            await qr_view.detach(session_id)

    async with _lock():
        current = _SESSIONS.get(session_id)
        if current is None:
            return
        _apply_result(current, result)
        current._finished_at = time.monotonic()


async def create_session(owner_user_id: int, target_shop_id: Optional[str], *,
                         login_mode="qr", username=None, password=None, remark=None) -> Tuple[Dict[str, Any], bool]:
    """创建并启动一次扫码会话。

    Args:
        owner_user_id: 发起用户 ID（归属绑定，后续所有访问都要一致）。
        target_shop_id: 重新授权时的目标店铺业务标识；新建店铺时传 None。

    Returns:
        ``(会话对外视图, 是否成功)``。超过并发上限时返回失败视图。
    """
    if login_mode not in {"qr", "password"} or (login_mode == "password" and (not username or not password)):
        return ({"state": QrOutcome.FAILED, "message": "账号和密码不能为空或登录方式无效"}, False)
    settings = get_settings()
    timeout_ms = int(getattr(settings, "qr_login_timeout_ms", 180_000) or 180_000)
    limit = max(1, int(getattr(settings, "qr_login_max_concurrent", 1) or 1))

    async with _lock():
        # 刷新页面或重复点击应恢复本人的同一目标会话，而非占用第二个浏览器。
        # 先清扫再计数，避免失效会话永远占住唯一并发名额。
        _sweep_locked()
        target = str(target_shop_id) if target_shop_id else None
        for active in _SESSIONS.values():
            if (login_mode == "qr" and active.login_mode == "qr" and not active.terminal
                    and active.owner_user_id == owner_user_id and active.target_shop_id == target):
                return (active.public_view(), True)
        if len([s for s in _SESSIONS.values() if not s.terminal]) >= limit:
            logger.warning("扫码并发已达上限 %s，拒绝创建新会话", limit)
            return ({"state": QrOutcome.FAILED, "message": "扫码通道繁忙，请稍后重试"}, False)
        session_id = _new_session_id()
        session = QrLoginSession(
            session_id=session_id,
            owner_user_id=owner_user_id,
            login_mode=login_mode,
            _login_credentials=({"username": username, "password": password} if login_mode == "password" else None),
            _remark=remark,
            target_shop_id=(str(target_shop_id) if target_shop_id else None),
            # 过期时间以北京时间 naive datetime 保存，与全仓时间口径一致（规范 17）。
            expires_at=now_beijing_naive() + timedelta(seconds=timeout_ms / 1000.0),
        )
        _SESSIONS[session_id] = session
        view = session.public_view()

    deadline_at = time.monotonic() + timeout_ms / 1000.0
    task = asyncio.create_task(_runner(session_id, deadline_at))
    async with _lock():
        current = _SESSIONS.get(session_id)
        if current is not None:
            current.task = task
    logger.info("已创建登录会话（mode=%s, owner=%s, target_shop=%s）", login_mode, owner_user_id, target_shop_id or "-")
    return (view, True)


async def get_session(session_id: str, owner_user_id: int) -> Optional[Dict[str, Any]]:
    """查询会话；归属不符或不存在一律返回 None（不区分二者）。

    顺带做一次轻量清扫：后台任务被杀或服务重启残留的"卡住"会话因此不会让前端
    无限轮询，最迟在下一次轮询时就会被强制落到明确终态。
    """
    async with _lock():
        _sweep_locked()
        session = _SESSIONS.get(session_id)
        if session is None or session.owner_user_id != owner_user_id:
            return None
        return session.public_view()


async def claim_session(session_id: str, owner_user_id: int) -> Tuple[Optional[Dict[str, Any]], str]:
    """领取会话凭据（一次性）。

    Returns:
        ``(凭据字典, 原因)``。原因取值：``ok`` / ``not_found`` / ``not_ready`` /
        ``already_claimed``。领取后状态转 ``claimed`` 并立即丢弃 Cookie。
    """
    async with _lock():
        session = _SESSIONS.get(session_id)
        if session is None or session.owner_user_id != owner_user_id:
            return (None, "not_found")
        if session.state == QrOutcome.CLAIMED:
            return (None, "already_claimed")
        if session.state != QrOutcome.CONFIRMED or not session._cookies:
            return (None, "not_ready")
        payload = {
            "cookies": session._cookies,
            "shop_id": session.shop_id,
            "shop_name": session.shop_name,
            "shop_logo": session.shop_logo,
            "username": session.username,
        }
        if session.login_mode == "password":
            payload.update(password=session._password, remark=session._remark)
        _clear_password(session)
        session.state = QrOutcome.CLAIMED
        session._cookies = None
        session.qr_png_b64 = None
        session._finished_at = time.monotonic()
        logger.info("扫码会话凭据已被领取一次（shop_id=%s，未记录内容）", session.shop_id)
        return (payload, "ok")


async def cancel_session(session_id: str, owner_user_id: int) -> Tuple[bool, str]:
    """取消会话：置取消信号并释放资源。

    Returns:
        ``(是否可取消, 原因)``。终态会话返回 ``(False, "terminal")``。
    """
    async with _lock():
        session = _SESSIONS.get(session_id)
        if session is None or session.owner_user_id != owner_user_id:
            return (False, "not_found")
        if session.terminal:
            return (False, "terminal")
        session.verification = None
        session.state = QrOutcome.CANCELLED
        session.message = "账号登录已取消" if session.login_mode == "password" else "扫码会话已取消"
        if session.cancel_event is not None:
            session.cancel_event.set()
        session.qr_png_b64 = None
        session._cookies = None
        _clear_password(session)
        session._finished_at = time.monotonic()
        task = session.task
    if task is not None and not task.done():
        task.cancel()
    logger.info("扫码会话已取消（owner=%s）", owner_user_id)
    return (True, "ok")


def reset_for_test() -> None:
    """清空注册表（仅供单元测试隔离使用，生产不调用）。"""
    global _LOCK, _SEMAPHORE
    for session_id in list(_SESSIONS):
        _discard_locked(session_id)
    _LOCK = None
    _SEMAPHORE = None


__all__ = [
    "QrLoginSession",
    "cancel_session",
    "claim_session",
    "create_session",
    "get_session",
    "reset_for_test",
    "update_display_state",
]
