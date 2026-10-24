# -*- coding: utf-8 -*-
"""
backend.app.services.qr_login_client —— 扫码登录服务间客户端
==========================================================
本文件用途：按「多服务拆分架构」把扫码登录的浏览器自动化留在 **websocket 服务**
执行，backend 只经服务间 HTTP 调用创建 / 查询 / 领取 / 取消扫码会话。地址经环境
变量 ``WEBSOCKET_SERVICE_URL`` 配置，**禁止写死 localhost**（规范 21 / 需求 25.4）。

对外提供：
- ``create_session(owner_user_id, target_shop_id)``：创建扫码会话。
- ``get_session(session_id, owner_user_id)``：查询状态（不含凭据）。
- ``claim_session(session_id, owner_user_id)``：一次性领取凭据。
- ``cancel_session(session_id, owner_user_id)``：取消并释放资源。

四者均返回 ``QrCallResult``：
- ``ok``：调用是否成功（传输层与业务层都通过才算成功）。
- ``data``：成功时的数据字典。
- ``message``：失败原因（中文，透传 websocket 侧文案）。

实现要点：
- 复用共通 ``common.services.service_client``（规范 36 / 52）。
- 网络不可达 / 超时 / 非 2xx / 业务失败一律规整为 ``ok=False``，不抛异常打断
  backend 主流程（健壮性兜底，需求 26）。
- ``claim`` 响应含 Cookie 明文，仅在内网流转且由调用方立即加密入库，
  **绝不再转发给前端**（需求 3.6）。本模块不落日志输出其内容。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

from common.services import service_client

logger = logging.getLogger(__name__)

# 扫码会话相关路径（与 websocket/routes/qr_login.py 约定一致）。
_SESSIONS_PATH: str = "/api/v1/qr-login/sessions"

# 创建 / 查询 / 取消都是即时返回（浏览器流程在 websocket 后台跑），用默认超时即可；
# 领取凭据涉及一次数据库无关的内存读取，同样短超时。
_QR_TIMEOUT_SECONDS: float = 20.0


@dataclass
class QrCallResult:
    """扫码会话服务间调用结果。"""

    ok: bool = False
    data: Optional[Dict[str, Any]] = None
    message: str = ""


def _invoke_json(method: str, path: str, *, payload: Optional[Dict[str, Any]] = None,
                 params: Optional[Dict[str, Any]] = None, action: str) -> QrCallResult:
    """发起服务间调用并规整为 ``QrCallResult``。

    Args:
        method: ``get`` 或 ``post``。
        path: 接口相对路径。
        payload: POST 请求体。
        params: GET 查询参数。
        action: 动作描述（仅用于日志，不含任何凭据）。

    Returns:
        规整后的调用结果；任何异常与失败均降级为 ``ok=False``。
    """
    base_url = service_client.websocket_base_url()
    if method == "get":
        response = service_client.get_json(
            base_url, path, params=params, timeout=_QR_TIMEOUT_SECONDS
        )
    else:
        response = service_client.post_json(
            base_url, path, payload or {}, timeout=_QR_TIMEOUT_SECONDS
        )

    if not response.ok or response.body is None:
        logger.warning("调用 websocket %s 失败：err=%s", action, response.message or response.error)
        return QrCallResult(ok=False, message="扫码登录服务暂不可用，请稍后重试")

    if not response.success:
        message = response.message or f"{action}失败"
        return QrCallResult(ok=False, message=str(message))

    data = response.data
    return QrCallResult(ok=True, data=data if isinstance(data, dict) else {}, message="")


def create_session(owner_user_id: int, target_shop_id: Optional[str]) -> QrCallResult:
    """创建扫码登录会话（需求：扫码绑定 / 重新授权）。

    Args:
        owner_user_id: 发起用户 ID，作为会话归属。
        target_shop_id: 重新授权时的目标店铺业务标识；新增店铺时传 None。

    Returns:
        ``QrCallResult``；成功时 ``data`` 为会话视图（含 session_id、过期时间）。
    """
    payload: Dict[str, Any] = {"owner_user_id": owner_user_id}
    if target_shop_id:
        payload["target_shop_id"] = str(target_shop_id)
    return _invoke_json("post", _SESSIONS_PATH, payload=payload, action="创建扫码会话")


def get_session(session_id: str, owner_user_id: int) -> QrCallResult:
    """查询扫码会话状态（返回体不含任何凭据）。"""
    return _invoke_json(
        "get",
        f"{_SESSIONS_PATH}/{session_id}",
        params={"owner_user_id": owner_user_id},
        action="查询扫码会话",
    )


def claim_session(session_id: str, owner_user_id: int) -> QrCallResult:
    """一次性领取扫码凭据（仅内网，调用方须立即加密入库且不得转发前端）。"""
    return _invoke_json(
        "post",
        f"{_SESSIONS_PATH}/{session_id}/claim",
        payload={"owner_user_id": owner_user_id},
        action="领取扫码凭据",
    )


def cancel_session(session_id: str, owner_user_id: int) -> QrCallResult:
    """取消扫码会话并释放其浏览器资源。

    走 websocket 侧的 ``POST .../cancel`` 形态：共通客户端 ``service_client`` 目前只
    提供 GET / POST 封装，不为一个接口扩张共通层（规范 36）。backend 对浏览器仍暴露
    ``DELETE /api/v1/qr-login/sessions/{id}``。
    """
    return _invoke_json(
        "post",
        f"{_SESSIONS_PATH}/{session_id}/cancel",
        payload={"owner_user_id": owner_user_id},
        action="取消扫码会话",
    )


__all__ = [
    "QrCallResult",
    "cancel_session",
    "claim_session",
    "create_session",
    "get_session",
]


def create_password_session(owner_user_id, target_shop_id, username, password, remark=None):
    return _invoke_json("post", _SESSIONS_PATH, payload={
        "owner_user_id": owner_user_id, "target_shop_id": target_shop_id,
        "login_mode": "password", "username": username, "password": password,
        "remark": remark}, action="创建账号密码登录会话")
