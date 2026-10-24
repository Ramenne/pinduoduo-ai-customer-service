# -*- coding: utf-8 -*-
"""
backend.app.services.email_code_service —— 邮箱验证码业务服务
=============================================================
本文件用途：实现「邮箱验证码」的下发与校验逻辑，供 captcha 路由与认证服务复用，
作为注册（参照 xianyu-auto-reply-wangpan 注册页）的邮箱归属校验：

- ``send_code(session, email, code_type)``：按场景校验邮箱（注册场景要求邮箱未
  被占用），限频（60 秒内仅可发送一次），生成 6 位数字验证码并经 SMTP 发送，
  同时在内存中按邮箱记录验证码（带 5 分钟过期）。
- ``verify_code(email, code, code_type)``：校验验证码是否正确且场景匹配，通过后
  立即作废（防重放）。

设计说明：
- 验证码保存在进程内内存字典中并带过期清理，与本项目其它验证码方案一致的单
  实例内存方案（规范 11：不涉及业务数据删除）。
- 邮件发送复用 smtp_proxy_service（读取系统 SMTP 配置），不写死任何外部地址
  （规范 21）；SMTP 未配置时返回中文提示，HTTP 仍恒 200（规范 1）。
- 邮箱唯一性最终以注册时的库层校验为准；本服务的占用校验为前置体验优化。

实现约束（开发规范）：
- 统一响应由路由层用 common.schemas.common 构造；本服务返回纯二元组。
- 导入置顶（规范 51）；中文注释完善（规范 37）；单文件 ≤500 行（规范 35）。
"""
from __future__ import annotations

import random
import re
import threading
import time
from typing import Dict, Optional, Tuple

from sqlalchemy.orm import Session

from app.services import smtp_proxy_service
from common.db.repository import Repository
from common.models.user_models import SysUser

# 验证码位数。
_CODE_LENGTH: int = 6
# 验证码有效期（秒）。
CODE_TTL: int = 300
# 同一邮箱两次发送的最小间隔（秒），用于限频。
_RESEND_INTERVAL: int = 60
# 支持的验证码场景。
_ALLOWED_TYPES: frozenset[str] = frozenset({"register", "login", "reset"})

# 邮箱格式校验正则（与前端口径一致的基础校验，避免引入额外依赖）。
_EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")

# ----------------------------------------------------------------------
# 进程内验证码存储（带过期），并用锁保证多线程安全
# ----------------------------------------------------------------------
# 邮箱验证码存储：{email: {"code": str, "type": str, "expires_at": float}}
_email_code_store: Dict[str, Dict[str, object]] = {}
_store_lock = threading.Lock()


def is_valid_email(email: str) -> bool:
    """校验邮箱格式是否合法（基础正则校验）。"""
    return bool(email) and bool(_EMAIL_PATTERN.match(email.strip()))


def _cleanup_expired(now: float) -> None:
    """清理已过期的邮箱验证码（调用方需已持有 _store_lock）。"""
    expired = [
        key for key, val in _email_code_store.items() if float(val["expires_at"]) < now
    ]
    for key in expired:
        _email_code_store.pop(key, None)


def _generate_code() -> str:
    """生成 _CODE_LENGTH 位数字验证码。"""
    return "".join(random.choices("0123456789", k=_CODE_LENGTH))


def send_code(session: Session, email: str, code_type: str = "register") -> Tuple[bool, str]:
    """下发邮箱验证码（含场景校验、限频、SMTP 发送与内存记录）。

    Args:
        session: 数据库会话（场景校验与读取 SMTP 配置）。
        email: 收件邮箱。
        code_type: 验证码场景（register/login/reset）。

    Returns:
        二元组 (是否成功, 中文提示)。
    """
    email = (email or "").strip()
    if not is_valid_email(email):
        return False, "请输入正确的邮箱地址"
    if code_type not in _ALLOWED_TYPES:
        return False, "验证码类型无效"

    # 场景校验：注册要求邮箱未被占用；登录 / 重置要求邮箱已注册。
    user_repo = Repository(SysUser, session)
    existing = user_repo.get_by(email=email)
    if code_type == "register" and existing is not None:
        return False, "该邮箱已被注册"
    if code_type in ("login", "reset") and existing is None:
        return False, "该邮箱未注册"

    now = time.time()
    with _store_lock:
        _cleanup_expired(now)
        # 限频：上次发送距今不足 _RESEND_INTERVAL 秒则拒绝。
        stored = _email_code_store.get(email)
        if stored is not None:
            sent_at = float(stored["expires_at"]) - CODE_TTL
            if now - sent_at < _RESEND_INTERVAL:
                return False, "验证码发送过于频繁，请稍后再试"
        code = _generate_code()
        _email_code_store[email] = {
            "code": code,
            "type": code_type,
            "expires_at": now + CODE_TTL,
        }

    # 经 SMTP 实际发送；失败则回滚内存记录，避免用户拿不到码却被记为已发送。
    ok, message = smtp_proxy_service.send_verification_code_email(
        session, email, code, code_type
    )
    if not ok:
        with _store_lock:
            _email_code_store.pop(email, None)
        return False, message
    return True, "验证码已发送到您的邮箱，请查收"


def verify_code(email: str, code: str, code_type: str = "register") -> Tuple[bool, str]:
    """校验邮箱验证码（场景需匹配），通过后立即作废（防重放）。

    Args:
        email: 收件邮箱。
        code: 用户输入的验证码。
        code_type: 期望的验证码场景。

    Returns:
        二元组 (是否通过, 中文提示)。
    """
    email = (email or "").strip()
    if not email or not code:
        return False, "请输入邮箱验证码"

    now = time.time()
    with _store_lock:
        _cleanup_expired(now)
        stored = _email_code_store.get(email)
        if stored is None:
            return False, "验证码不存在或已过期"
        if float(stored["expires_at"]) < now:
            _email_code_store.pop(email, None)
            return False, "验证码已过期"
        if str(stored["code"]) != code.strip():
            return False, "验证码错误"
        if str(stored["type"]) != code_type:
            return False, "验证码类型不匹配"
        # 校验通过：立即作废。
        _email_code_store.pop(email, None)
    return True, "验证码验证成功"


__all__ = [
    "is_valid_email",
    "send_code",
    "verify_code",
    "CODE_TTL",
]
