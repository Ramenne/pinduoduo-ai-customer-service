# -*- coding: utf-8 -*-
"""
backend.app.api.routes.qr_login —— 扫码登录接口路由
==================================================
本文件用途：对外提供「拼多多店铺扫码登录」的 REST 接口，满足本轮新增能力：在管理
后台展示真实登录二维码，用户手机扫码确认后由系统核验身份并保存登录态，减少手工
粘贴 Cookie。

- ``POST   /qr-login/sessions``                  创建扫码会话（新增或重新授权）。
- ``GET    /qr-login/sessions/{session_id}``     轮询会话状态（不含任何凭据）。
- ``POST   /qr-login/sessions/{session_id}/bind`` 确认完成后落库绑定（一次性、幂等）。
- ``DELETE /qr-login/sessions/{session_id}``      取消会话并释放资源。

为什么多出一个 ``bind``：状态查询会被前端高频轮询，若确认完成时后端自动落库，则
「查询」这个只读动作会携带写副作用，重复轮询就可能重复绑定。故把「领取凭据 + 落库」
显式拆成一次 ``bind`` 调用，前端只在收到 ``confirmed`` 后调用一次，语义清晰且幂等。

权限与隔离（需求 2.4 / 3.7）：创建与绑定需 ``shop:create`` 权限；会话归属绑定发起
用户，其他用户查询 / 取消 / 绑定一律按「会话不存在」处理，管理员不旁路。

**响应中绝不含 Cookie**：二维码图片以 base64 随状态返回（它本身就是登录入口，故
只回给归属用户本人、且不落普通日志）；凭据只在服务间内网流转并立即加密入库。

接口约定（开发规范 1-3）：HTTP 恒 200，业务成败由统一响应体表达。
"""
from __future__ import annotations

from typing import Optional, Literal

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field, SecretStr
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.services import qr_login_service, qr_login_client
from common.db.session import get_db
from common.models.user_models import SysUser
from common.schemas.common import ApiResponse, success_response, error_response

# 扫码登录路由：标签便于 OpenAPI 分组；前缀由聚合层添加。
router = APIRouter(tags=["扫码登录"])


class CreateQrSessionRequest(BaseModel):
    """创建扫码会话请求体。"""

    login_mode: Literal["qr", "password"] = "qr"
    username: Optional[str] = Field(None, max_length=256)
    password: Optional[SecretStr] = Field(None, max_length=512)
    remark: Optional[str] = Field(None, max_length=1000)
    target_shop_id: Optional[str] = Field(
        None,
        description="重新授权时必填：目标店铺业务标识；扫码确认到的店铺与之一致才会落库",
    )


@router.post(
    "/qr-login/sessions",
    response_model=ApiResponse,
    summary="创建扫码登录会话",
)
def create_qr_session(
    payload: CreateQrSessionRequest,
    current_user: SysUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """创建一次扫码登录会话并返回二维码。

    Args:
        payload: 可选携带 target_shop_id（重新授权场景）。
        current_user: 当前登录用户，成为会话归属。
        db: 数据库会话。

    Returns:
        统一响应体：成功返回会话视图（session_id / 二维码 base64 / 过期时间）；
        无权限、目标店铺不属于当前用户或扫码服务不可用时返回失败响应。
    """
    options = {}
    if payload.login_mode == "password":
        options = {"login_mode": "password", "username": payload.username,
                   "password": payload.password.get_secret_value() if payload.password else None,
                   "remark": payload.remark}
    return qr_login_service.create_qr_session(
        db, current_user, target_shop_id=payload.target_shop_id, **options)


@router.get(
    "/qr-login/sessions/{session_id}",
    response_model=ApiResponse,
    summary="查询扫码会话状态",
)
def get_qr_session(
    session_id: str,
    current_user: SysUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """轮询扫码会话状态。

    Args:
        session_id: 会话标识。
        current_user: 当前登录用户。
        db: 数据库会话（与其他服务函数签名保持一致）。

    Returns:
        统一响应体：进行中返回状态与最新二维码；进入失败类终态时返回可区分的业务码
        （42604 需人工验证 / 42605 已过期 / 42602 失败）。
    """
    return qr_login_service.get_qr_session(db, current_user, session_id=session_id)


@router.post(
    "/qr-login/sessions/{session_id}/bind",
    response_model=ApiResponse,
    summary="绑定扫码结果（核验通过后落库）",
)
def bind_qr_session(
    session_id: str,
    current_user: SysUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """把已确认且身份核验通过的扫码结果落库。

    Args:
        session_id: 会话标识。
        current_user: 当前登录用户（凭据归属人）。
        db: 数据库会话。

    Returns:
        统一响应体：成功返回脱敏后的店铺信息（前端据此才显示「绑定成功」）；
        扫码店铺与目标店铺不一致返回 42606 且**不改动原凭据**；重复调用幂等。
    """
    return qr_login_service.bind_qr_session(db, current_user, session_id=session_id)


@router.delete(
    "/qr-login/sessions/{session_id}",
    response_model=ApiResponse,
    summary="取消扫码会话",
)
def cancel_qr_session(
    session_id: str,
    current_user: SysUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """取消扫码会话，关闭其浏览器资源并清理临时二维码。

    Args:
        session_id: 会话标识。
        current_user: 当前登录用户。
        db: 数据库会话。

    Returns:
        统一响应体：取消成功返回成功；会话已结束或不存在返回对应提示。
    """
    return qr_login_service.cancel_qr_session(db, current_user, session_id=session_id)


__all__ = ["router"]


class WindowInput(BaseModel):
    kind: str
    x: Optional[float] = None
    y: Optional[float] = None
    key: Optional[str] = None
    text: Optional[SecretStr] = Field(None, max_length=256)
    delta_x: Optional[float] = None
    delta_y: Optional[float] = None


@router.get("/qr-login/sessions/{session_id}/frame", response_model=ApiResponse)
def window_frame(session_id: str, response: Response,
                 current_user: SysUser = Depends(get_current_user)):
    response.headers["Cache-Control"] = "no-store"
    result = qr_login_client._invoke_json(
        "get", f"/api/v1/qr-login/sessions/{session_id}/frame",
        params={"owner_user_id": current_user.id}, action="获取人工验证画面")
    return success_response(data=result.data) if result.ok else error_response(42602, result.message)


@router.post("/qr-login/sessions/{session_id}/input", response_model=ApiResponse)
def window_input(session_id: str, payload: WindowInput,
                 current_user: SysUser = Depends(get_current_user)):
    event = payload.model_dump()
    if payload.text is not None:
        event["text"] = payload.text.get_secret_value()
    result = qr_login_client._invoke_json(
        "post", f"/api/v1/qr-login/sessions/{session_id}/input",
        payload={**event, "owner_user_id": current_user.id}, action="转发用户操作")
    return success_response() if result.ok else error_response(42602, result.message)


class VerificationAction(BaseModel):
    action: Literal["submit_sms", "resend_sms"]
    code: Optional[SecretStr] = Field(None, max_length=8)


@router.post("/qr-login/sessions/{session_id}/verification", response_model=ApiResponse)
def verification_action(session_id: str, payload: VerificationAction,
                        current_user: SysUser = Depends(get_current_user)):
    result = qr_login_client._invoke_json("post", f"/api/v1/qr-login/sessions/{session_id}/verification",
        payload={"owner_user_id": current_user.id, "action": payload.action,
                 "code": payload.code.get_secret_value() if payload.code else None}, action="提交短信验证操作")
    return success_response(message="操作已提交，请等待平台结果") if result.ok else error_response(42602, result.message)
