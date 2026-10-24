"""单账号续期编排：当前凭据为权威，身份确认后CAS保存，失败分型且有界。"""
import asyncio
from dataclasses import dataclass, field
import logging
import threading
import time

from channel_pdd.core.credential_store import credential_snapshot, save_session_observation

logger = logging.getLogger('channel_pdd.session_health')
_locks = {}
_guard = threading.Lock()
_capacity = threading.BoundedSemaphore(2)
_cache = {}
REFRESH_SECONDS = 1800
FAILURE_SECONDS = 120
HUMAN_SECONDS = 900


@dataclass
class SessionResult:
    state: str
    reason: str = ''
    cookies: dict | None = field(default=None, repr=False)
    snapshot: object = field(default=None, repr=False)


def probe_identity(snapshot, cookies):
    """仅查询身份，不隐式重登，也不重复POST业务操作。"""
    import requests
    from channel_pdd.core.base_request import BaseRequest
    from channel_pdd.api.get_user_info import USER_INFO_URL
    try:
        with requests.Session() as client:
            response = client.post(USER_INFO_URL, data='', cookies=cookies,
                headers=BaseRequest().default_headers, timeout=(3, 8), allow_redirects=False)
            if response.status_code == 401:
                return SessionResult('expired', 'identity_unauthorized')
            if response.status_code != 200:
                return SessionResult('temporary_error', 'identity_http_error')
            try:
                data = response.json()
            except ValueError:
                return SessionResult('temporary_error', 'identity_non_json')
            if not isinstance(data, dict):
                return SessionResult('temporary_error', 'identity_invalid_shape')
            if data.get('error_code') == 43001 and '会话已过期' in str(data.get('error_msg', '')):
                return SessionResult('expired', 'identity_session_expired')
            if data.get('success') is not True:
                return SessionResult('temporary_error', 'identity_unconfirmed')
            identity = data.get('result') or {}
            if not isinstance(identity, dict):
                return SessionResult('temporary_error', 'identity_invalid_shape')
            if str(identity.get('mall_id')) != snapshot.shop_id:
                return SessionResult('temporary_error', 'identity_mismatch')
            # 保留既有签名等Cookie，只合并本次平台正式响应签发的Cookie。
            merged = dict(cookies)
            merged.update(client.cookies.get_dict())
            return SessionResult('online', cookies=merged)
    except requests.RequestException:
        return SessionResult('temporary_error', 'identity_network_error')


def _refresh_browser(cookies):
    from login.session_refresh import refresh_from_cookies
    try:
        return asyncio.run(refresh_from_cookies(cookies))
    except Exception:
        # 不把浏览器启动/超时/网络异常合并成过期，更不清除Cookie。
        return {'state': 'temporary_error', 'reason': 'browser_refresh_failed'}


def maintain_session(shop_id, user_id, *, force=False, renew=True, cancelled=None, rejected=False):
    if cancelled is not None and cancelled.is_set():
        return SessionResult('stale', 'operation_cancelled')
    key = (str(shop_id), user_id)
    with _guard:
        lock = _locks.setdefault(key, threading.Lock())
    if not lock.acquire(blocking=False):
        return SessionResult('busy')
    acquired = False
    try:
        snapshot = credential_snapshot(shop_id, user_id)
        if snapshot is None:
            return SessionResult('stale', 'account_unavailable')
        cached = _cache.get(key)
        if cached and cached[0] == snapshot.encrypted and time.monotonic() < cached[1] and not force and not (rejected and cached[2] == 'online'):
            # 旧结果只用于限流，不把旧Cookie当成新授权再次返回。
            return SessionResult(cached[2], 'cooldown',
                                 snapshot.cookies if cached[2] == 'online' else None, snapshot)
        acquired = _capacity.acquire(blocking=False)
        if not acquired:
            return SessionResult('busy')
        result = probe_identity(snapshot, snapshot.cookies)
        proven = result.state == 'online'
        candidate = result.cookies
        if renew and result.state in ('online', 'expired'):
            refreshed = _refresh_browser(candidate or snapshot.cookies)
            if refreshed.get('state') == 'candidate':
                checked = probe_identity(snapshot, refreshed.get('cookies') or {})
                if checked.state == 'online':
                    result, candidate = checked, checked.cookies
                elif not proven:
                    result = checked
                else:
                    result.reason = 'renewal_deferred'
            elif not proven:
                result = SessionResult(refreshed.get('state', 'temporary_error'), refreshed.get('reason', ''))
            else:
                result.reason = 'renewal_deferred'
            # 浏览器刷新失败不能抹去刚刚被平台身份API确认有效的原Cookie。
        if cancelled is not None and cancelled.is_set():
            return SessionResult('stale', 'operation_cancelled')
        if result.state == 'online':
            candidate = candidate or snapshot.cookies
            new_cookies = candidate if candidate != snapshot.cookies else None
            saved = save_session_observation(snapshot, 'online', new_cookies)
        elif result.state in ('expired', 'verification_required'):
            saved = save_session_observation(snapshot, 'relogin')
        else:
            saved = True  # 临时错误保持已知登录状态和凭据。
        if not saved:
            return SessionResult('stale', 'newer_authorization_preserved')
        current = credential_snapshot(shop_id, user_id)
        if current is None:
            return SessionResult('stale', 'account_unavailable')
        # 保存后若有人再次授权，不把本轮结果写入新授权的冷却窗口。
        expected = candidate if result.state == 'online' else snapshot.cookies
        if current.cookies != expected:
            return SessionResult('stale', 'newer_authorization_preserved')
        delay = REFRESH_SECONDS if result.state == 'online' and result.reason != 'renewal_deferred' else HUMAN_SECONDS if result.state in ('expired', 'verification_required') else FAILURE_SECONDS
        _cache[key] = (current.encrypted, time.monotonic() + delay, result.state)
        return SessionResult(result.state, result.reason,
            current.cookies if result.state == 'online' else None, current)
    finally:
        if acquired:
            _capacity.release()
        lock.release()


class SessionMaintenance:
    def __init__(self, shop_id, user_id):
        self.shop_id, self.user_id = shop_id, user_id
        self.cancelled = threading.Event()

    def stop(self):
        self.cancelled.set()

    async def run(self):
        # 启动连接本身验证Token；定时维护不与初始建连竞争浏览器。
        delay = REFRESH_SECONDS
        while True:
            await asyncio.sleep(delay)
            try:
                result = await asyncio.to_thread(maintain_session, self.shop_id, self.user_id,
                                                cancelled=self.cancelled)
                logger.info('登录维护完成 state=%s reason=%s', result.state, result.reason)
                delay = REFRESH_SECONDS if result.state == 'online' and result.reason != 'renewal_deferred' else HUMAN_SECONDS if result.state in ('expired', 'verification_required') else FAILURE_SECONDS
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.error('登录维护暂时失败，保留当前授权')
                delay = FAILURE_SECONDS
