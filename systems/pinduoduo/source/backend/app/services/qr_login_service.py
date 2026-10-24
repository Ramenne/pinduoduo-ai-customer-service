# -*- coding: utf-8 -*-
"""
backend.app.services.qr_login_service —— 扫码登录业务服务
========================================================
本文件用途：实现 backend 侧「扫码登录」的业务逻辑，供 qr_login 路由复用。浏览器
自动化与状态机在 websocket 服务，本模块只负责**权限、归属、身份一致性、落库与幂等**。

职责：
- ``create_qr_session``：权限校验后创建扫码会话；重新授权时先确认目标店铺属于当前
  用户，并把 ``target_shop_id`` 绑定到会话上。
- ``get_qr_session``：查询状态；二次校验返回体里的归属用户，并把终态映射成**可区分**
  的业务码（需人工验证 / 已过期 / 失败），不再笼统报"超时"。
- ``bind_qr_session``：扫码确认且身份核验通过后，一次性领取凭据并落库，随后通知
  websocket 以最新凭据重连。
- ``cancel_qr_session``：取消会话。

四条硬性安全约束（对应本轮实现要求）：
1. **凭据不落前端**：只有 ``bind_qr_session`` 会经内网领取 Cookie，且领取后立刻交给
   既有的可逆加密路径入库；对外响应一律经 ``serialize_shop`` 脱敏（需求 3.6）。
2. **错误店铺不得覆盖授权**：重新授权时，扫码确认到的 ``shop_id`` 必须与创建会话时
   绑定的 ``target_shop_id`` 完全一致，不一致直接拒绝且**不写任何数据**，原凭据保持
   不变（业务码 42606）。
3. **失败不得破坏原凭据**：任何核验/领取失败路径都在写库之前返回。
4. **重复查询不得重复绑定**：websocket 侧的领取是一次性的，本模块在收到「已领取」
   时改为查询既有店铺并返回幂等成功，不重复 upsert。

落库复用既有的 ``account_service.upsert_shop`` / ``update_shop``（规范 36），因此
加密口径、数据范围隔离与「先提交再通知连接」的时序都与账号密码登录 / Cookie 导入
完全一致，不引入第二套写库路径。

实现约束（开发规范）：统一响应体、HTTP 恒 200（1-3）；参数化查询（16）；
中文注释（37）；导入置顶（51）；时间北京时间（17）；单文件 ≤500 行（35）。
"""
from __future__ import annotations

import json
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from app.core import permission
from app.core.data_scope import is_in_scope, load_user_scope
from app.core.business_codes import (
    CODE_EXTERNAL_ERROR,
    CODE_FORBIDDEN,
    CODE_NOT_FOUND,
    CODE_PARAM_ERROR,
    CODE_QR_EXPIRED,
    CODE_QR_VERIFICATION_REQUIRED,
    CODE_SHOP_IDENTITY_MISMATCH,
    MSG_FORBIDDEN,
    MSG_QR_EXPIRED,
    MSG_QR_VERIFICATION_REQUIRED,
    MSG_SHOP_IDENTITY_MISMATCH,
)
from app.services import account_service, qr_login_client
from common.db.repository import Repository
from common.models.shop_models import Shop
from common.models.user_models import SysUser
from common.schemas.common import ApiResponse, error_response, success_response

# 受保护资源键（与 shops 路由一致：扫码登录本质是创建 / 重新授权店铺）。
RESOURCE_SHOP: str = "shop"

# websocket 会话状态 → backend 业务码映射。**仅用于 bind**：状态查询本身不再把
# verification_required 当错误抛出（见 get_qr_session 的说明），否则前端无法在
# 用户完成验证后继续等待。
_STATE_ERROR_CODES: Dict[str, int] = {
    "verification_required": CODE_QR_VERIFICATION_REQUIRED,
    "expired": CODE_QR_EXPIRED,
    "failed": CODE_EXTERNAL_ERROR,
    "cancelled": CODE_QR_EXPIRED,
}

# 默认中文提示（websocket 未给出文案时兜底）。
_STATE_FALLBACK_MESSAGES: Dict[str, str] = {
    "verification_required": MSG_QR_VERIFICATION_REQUIRED,
    "expired": MSG_QR_EXPIRED,
    "failed": "扫码登录失败，请稍后重试",
    "cancelled": "扫码会话已取消",
}

# 可对外呈现的会话字段（**绝不含 Cookie**）。
# 含 worker_pid：扫码会话只存在于创建它的进程内存里，前端据此可在"创建时的 pid 与
# 轮询返回的 pid 不一致"时给出准确原因（多 worker 部署），而不是含糊的"会话不存在"。
_PUBLIC_FIELDS: tuple[str, ...] = (
    "session_id",
    "login_mode",
    "verification",
    "state",
    "terminal",
    "message",
    "qr_image_base64",
    "qr_rotation",
    "shop_id",
    "shop_name",
    "target_shop_id",
    "created_at",
    "expires_at",
    "worker_pid",
)


def _ensure_permission(user: SysUser, action: str, session: Session) -> Optional[ApiResponse]:
    """统一权限校验：未授权返回「无访问权限」响应体，已授权返回 None。"""
    if permission.check(user, RESOURCE_SHOP, action, session=session):
        return None
    return error_response(CODE_FORBIDDEN, MSG_FORBIDDEN)


def _public_view(data: Dict[str, Any]) -> Dict[str, Any]:
    """从 websocket 会话视图中挑出允许转发给前端的字段。"""
    return {key: data.get(key) for key in _PUBLIC_FIELDS if key in data}


def _find_own_shop(session: Session, *, owner_user_id: int, shop_id: str) -> Optional[Shop]:
    """按全局店铺身份查找，并应用与列表、编辑相同的数据权限。"""
    if not shop_id:
        return None
    shop = Repository(Shop, session).get_by(canonical_shop_id=str(shop_id))
    if shop is None or not is_in_scope(load_user_scope(session, owner_user_id), shop.owner_user_id):
        return None
    return shop


def create_qr_session(
    session: Session,
    current_user: SysUser,
    *,
    target_shop_id: Optional[str] = None,
    login_mode="qr", username=None, password=None, remark=None,
) -> ApiResponse:
    """创建扫码登录会话（新增店铺或为已有店铺重新授权）。

    Args:
        session: 数据库会话。
        current_user: 当前登录用户（成为会话归属）。
        target_shop_id: 重新授权的目标店铺业务标识；新增店铺时留空。

    Returns:
        统一响应体：成功返回会话视图（含 session_id 与二维码图片，不含凭据）；
        无权限、目标店铺不属于当前用户或扫码服务不可用时返回对应失败响应。
    """
    denied = _ensure_permission(current_user, "create", session)
    if denied is not None:
        return denied

    bound_target: Optional[str] = None
    if target_shop_id:
        shop = _find_own_shop(
            session, owner_user_id=current_user.id, shop_id=str(target_shop_id)
        )
        if shop is None:
            # 与 update_shop 的越权口径一致：不区分「不存在」与「不属于你」。
            return error_response(CODE_NOT_FOUND, "目标店铺不存在")
        bound_target = str(shop.shop_id)

    if login_mode == "password":
        if not username or not str(username).strip() or not password:
            return error_response(CODE_PARAM_ERROR, "账号和密码不能为空")
        result = qr_login_client.create_password_session(
            current_user.id, bound_target, str(username).strip(), password, remark)
    elif login_mode == "qr":
        result = qr_login_client.create_session(current_user.id, bound_target)
    else:
        return error_response(CODE_PARAM_ERROR, "不支持的登录方式")
    if not result.ok:
        return error_response(CODE_EXTERNAL_ERROR, result.message or "扫码登录服务暂不可用")

    return success_response(data=_public_view(result.data or {}), message="扫码会话已创建")


def get_qr_session(
    session: Session,
    current_user: SysUser,
    *,
    session_id: str,
) -> ApiResponse:
    """查询扫码会话状态（前端轮询用，返回体不含任何凭据）。

    **所有会话状态都以 success 返回**，由响应里的 ``terminal`` 字段告诉前端是否
    该停止轮询。刻意不把 ``verification_required`` 做成业务错误：用户本人完成安全
    验证后流程还能继续走到成功，若在这里就报错，前端会停表、用户失去机会。
    失败类业务码只保留给 ``bind`` 与「会话不存在」。

    Args:
        session: 数据库会话。
        current_user: 当前登录用户。
        session_id: 会话标识。

    Returns:
        统一响应体：``data`` 含 ``state`` / ``terminal`` / ``qr_image_base64`` 等；
        会话不存在或不属于当前用户时返回 40400。
    """
    result = qr_login_client.get_session(session_id, current_user.id)
    if not result.ok:
        return error_response(CODE_NOT_FOUND, result.message or "会话不存在或已结束")

    data = result.data or {}
    # 二次归属校验：websocket 已按 owner 过滤，这里再确认一次，防止上游实现退化。
    if data.get("owner_user_id") not in (None, current_user.id):
        return error_response(CODE_NOT_FOUND, "会话不存在或已结束")

    return success_response(data=_public_view(data), message="查询成功")


def bind_qr_session(
    session: Session,
    current_user: SysUser,
    *,
    session_id: str,
) -> ApiResponse:
    """把已确认的扫码登录结果落库（一次性、幂等、校验店铺一致性）。

    流程：领取凭据 → 校验身份与目标店铺一致 → 复用既有 upsert 加密落库 →
    由既有写库路径通知 websocket 重连。任何一步失败都在写库前返回，原凭据不动。

    Args:
        session: 数据库会话。
        current_user: 当前登录用户（凭据归属人）。
        session_id: 扫码会话标识。

    Returns:
        统一响应体：成功返回脱敏后的店铺信息；身份不一致返回 42606；
        重复调用返回幂等成功或明确原因。
    """
    denied = _ensure_permission(current_user, "create", session)
    if denied is not None:
        return denied

    view_result = qr_login_client.get_session(session_id, current_user.id)
    if not view_result.ok:
        return error_response(CODE_NOT_FOUND, view_result.message or "会话不存在或已结束")
    view = view_result.data or {}
    state = str(view.get("state") or "")
    target_shop_id = view.get("target_shop_id")

    # 「已领取」用状态判定，不靠匹配错误文案 —— 文案会改，状态是契约。
    if state == "claimed":
        return _idempotent_bound_response(
            session, current_user,
            # 新建绑定时 target_shop_id 为空，此时用会话上记录的扫码结果店铺标识兜底，
            # 否则同一会话的第二次 bind 会因为查不到店铺而报"请重新发起"。
            shop_ids=(view.get("shop_id"), target_shop_id),
        )

    if state in _STATE_ERROR_CODES:
        return error_response(
            _STATE_ERROR_CODES[state],
            view.get("message") or _STATE_FALLBACK_MESSAGES.get(state, "扫码登录未完成"),
        )
    if state != "confirmed":
        return error_response(CODE_PARAM_ERROR, "扫码尚未完成确认，暂不能绑定")

    claim = qr_login_client.claim_session(session_id, current_user.id)
    if not claim.ok:
        # 并发绑定：前一个请求可能刚好在本次 get 与 claim 之间完成了领取。
        # 此时应给幂等成功，而不是把一个已经绑定好的会话报成错误。
        recheck = qr_login_client.get_session(session_id, current_user.id)
        if recheck.ok and str((recheck.data or {}).get("state") or "") == "claimed":
            view = recheck.data or {}
            return _idempotent_bound_response(
                session, current_user,
                shop_ids=(view.get("shop_id"), view.get("target_shop_id")),
            )
        # 其余情况一律不写库，交由前端重试或重新发起扫码。
        return error_response(
            CODE_EXTERNAL_ERROR, claim.message or "扫码结果已被领取，请重新发起扫码"
        )

    payload = claim.data or {}
    scanned_shop_id = str(payload.get("shop_id") or "")
    cookies = payload.get("cookies")

    if not scanned_shop_id or not cookies:
        # 领取成功但信息不全：不落库，避免用空凭据覆盖已有授权。
        return error_response(CODE_PARAM_ERROR, "扫码结果缺少店铺标识或凭据，未做任何更改")

    # 身份一致性：重新授权时，扫码确认到的店铺必须就是发起时选定的那一家。
    if target_shop_id and scanned_shop_id != str(target_shop_id):
        return error_response(
            CODE_SHOP_IDENTITY_MISMATCH,
            f"{MSG_SHOP_IDENTITY_MISMATCH}（目标 {target_shop_id}，实际扫码 {scanned_shop_id}）",
        )

    cookies_text = cookies if isinstance(cookies, str) else json.dumps(cookies, ensure_ascii=False)
    existing = _find_own_shop(
        session, owner_user_id=current_user.id, shop_id=scanned_shop_id
    )

    password_fields = {}
    if view.get("login_mode") == "password":
        password_fields = {"password": payload.get("password"), "remark": payload.get("remark")}
    if existing is not None:
        # 该用户名下已有此店铺：只更新凭据，走既有 update_shop 路径（含重连通知）。
        return account_service.update_shop(
            session,
            existing.id,
            current_user=current_user,
            shop_name=payload.get("shop_name") or None,
            cookies=cookies_text,
            username=payload.get("username") or None,
            **password_fields,
        )

    return account_service.upsert_shop(
        session,
        shop_id=scanned_shop_id,
        owner_user_id=current_user.id,
        shop_name=payload.get("shop_name"),
        shop_logo=payload.get("shop_logo"),
        cookies=cookies_text,
        username=payload.get("username"),
        password=password_fields.get("password"),
        remark=password_fields.get("remark"),
        operator_id=current_user.id,
    )


def _idempotent_bound_response(
    session: Session,
    current_user: SysUser,
    *,
    shop_ids: tuple,
) -> ApiResponse:
    """会话已被领取过时的幂等应答：能定位到已绑定店铺就报成功，否则报原因。

    Args:
        session: 数据库会话。
        current_user: 当前登录用户。
        shop_ids: 候选店铺业务标识（按优先级排列，取第一个非空值）。
    """
    shop_id = next((str(v) for v in shop_ids if v), "")
    existing = (
        _find_own_shop(session, owner_user_id=current_user.id, shop_id=shop_id)
        if shop_id
        else None
    )
    if existing is not None:
        return success_response(
            data=account_service.serialize_shop(existing),
            message="该扫码会话已完成绑定，未重复更改",
        )
    return error_response(CODE_PARAM_ERROR, "该扫码会话已完成绑定，请重新发起扫码")


def cancel_qr_session(
    session: Session,
    current_user: SysUser,
    *,
    session_id: str,
) -> ApiResponse:
    """取消扫码会话并释放其浏览器资源与临时二维码。

    Args:
        session: 数据库会话（保留形参以与其他服务函数签名一致）。
        current_user: 当前登录用户。
        session_id: 会话标识。

    Returns:
        统一响应体：取消成功返回成功；会话已结束或不存在返回对应提示。
    """
    result = qr_login_client.cancel_session(session_id, current_user.id)
    if not result.ok:
        return error_response(CODE_NOT_FOUND, result.message or "会话不存在或已结束")
    return success_response(data={"session_id": session_id}, message="扫码会话已取消")


__all__ = [
    "bind_qr_session",
    "cancel_qr_session",
    "create_qr_session",
    "get_qr_session",
]
