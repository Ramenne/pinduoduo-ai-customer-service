# -*- coding: utf-8 -*-
"""
websocket.routes.qr_login —— 扫码登录接口（供 backend 调用）
==========================================================
本文件用途：把 ``login.qr_session_store`` 提供的扫码登录会话能力以 HTTP 形式暴露给
backend 服务，供其在「添加店铺 / 重新授权」时创建会话、轮询状态、领取凭据与取消。

- ``POST   /qr-login/sessions``：创建扫码会话并启动后台浏览器流程。
- ``GET    /qr-login/sessions/{session_id}``：查询会话状态（**不含任何凭据**）。
- ``POST   /qr-login/sessions/{session_id}/claim``：一次性领取登录凭据。
- ``DELETE /qr-login/sessions/{session_id}``：取消会话并释放浏览器资源。

归属约束：四个接口都要求调用方带上 ``owner_user_id``，注册表内部严格比对；
不一致一律按「会话不存在」返回，避免泄露他人会话是否存在。管理员不旁路。

一次性领取的设计理由：状态查询会被前端高频轮询，若每次都带回 Cookie，凭据就会
反复穿越服务间边界并可能被日志捕获。故凭据只在 ``confirmed -> claimed`` 这一次
转换中返回，返回后立即从内存丢弃 —— 结构上杜绝「重复轮询导致重复绑定」。

接口约定（开发规范 1-3）：HTTP 恒 200，业务成败由统一响应体表达；本路由不抛异常，
失败统一规整为失败响应（健壮性兜底，需求 26）。地址经环境变量配置（规范 21）。

安全说明：``claim`` 响应包含 Cookie 明文，仅用于 **服务间内网调用**，backend 收到后
立即经既有可逆加密入库，绝不再转发给前端（需求 3.6）。日志只记录状态与店铺标识，
不记录 Cookie、Token 或二维码内容。
"""
from __future__ import annotations

import logging
from typing import Optional, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field, SecretStr

from common.schemas.common import ApiResponse, error_response, success_response
from login import qr_session_store, qr_view
from login.qr_state import QrOutcome

logger = logging.getLogger("websocket.routes.qr_login")

# 扫码登录路由：标签便于 OpenAPI 分组；前缀由聚合层添加。
router = APIRouter(tags=["扫码登录"])


class QrSessionCreateRequest(BaseModel):
    """创建扫码会话请求体。"""

    owner_user_id: int = Field(..., description="发起用户 ID（归属绑定）")
    login_mode: Literal["qr", "password"] = "qr"
    username: Optional[str] = Field(None, max_length=256)
    password: Optional[SecretStr] = Field(None, max_length=512)
    remark: Optional[str] = Field(None, max_length=1000)
    target_shop_id: Optional[str] = Field(
        None, description="重新授权时的目标店铺业务标识；新增店铺时留空"
    )


class QrSessionClaimRequest(BaseModel):
    """领取扫码会话凭据请求体。"""

    owner_user_id: int = Field(..., description="发起用户 ID，须与会话归属一致")


@router.post("/qr-login/sessions", response_model=ApiResponse, summary="创建扫码登录会话")
async def create_qr_session(payload: QrSessionCreateRequest) -> ApiResponse:
    """创建扫码登录会话并启动后台浏览器流程。

    Args:
        payload: 含 owner_user_id 与可选 target_shop_id 的请求体。

    Returns:
        统一响应体：成功返回会话视图（含 session_id 与过期时间，**不含凭据**）；
        并发已满或流程无法启动时返回失败响应。
    """
    try:
        options = {}
        if payload.login_mode == "password":
            options = {"login_mode": "password", "username": payload.username,
                       "password": payload.password.get_secret_value() if payload.password else None,
                       "remark": payload.remark}
        view, ok = await qr_session_store.create_session(
            owner_user_id=payload.owner_user_id, target_shop_id=payload.target_shop_id, **options)
    except Exception as exc:  # noqa: BLE001 - 路由层不抛异常
        logger.error("创建扫码会话异常：%s", exc)
        return error_response(-1, "扫码登录服务暂不可用，请稍后重试")

    if not ok:
        return error_response(-1, view.get("message") or "扫码登录服务繁忙")
    return success_response(data=view, message="扫码会话已创建")


@router.get("/qr-login/sessions/{session_id}", response_model=ApiResponse, summary="查询扫码会话状态")
async def get_qr_session(
    session_id: str,
    owner_user_id: int = Query(..., description="发起用户 ID，须与会话归属一致"),
) -> ApiResponse:
    """查询扫码会话状态（不含任何凭据）。

    Args:
        session_id: 会话标识。
        owner_user_id: 归属用户 ID。

    Returns:
        统一响应体：成功返回会话视图；不存在或归属不符返回「会话不存在或已结束」。
    """
    try:
        view = await qr_session_store.get_session(session_id, owner_user_id)
    except Exception as exc:  # noqa: BLE001
        logger.error("查询扫码会话异常：%s", exc)
        return error_response(-1, "扫码登录服务暂不可用，请稍后重试")

    if view is None:
        return error_response(-1, "会话不存在或已结束，请重新发起扫码")
    return success_response(data=view, message="查询成功")


@router.post(
    "/qr-login/sessions/{session_id}/claim",
    response_model=ApiResponse,
    summary="一次性领取扫码会话凭据",
)
async def claim_qr_session(session_id: str, payload: QrSessionClaimRequest) -> ApiResponse:
    """一次性领取扫码所得凭据（供 backend 落库后立即丢弃，不再转发前端）。

    Args:
        session_id: 会话标识。
        payload: 含 owner_user_id 的请求体。

    Returns:
        统一响应体：领取成功返回凭据与店铺标识；尚未确认 / 已领取 / 无权分别返回
        对应中文原因。
    """
    try:
        data, reason = await qr_session_store.claim_session(session_id, payload.owner_user_id)
    except Exception as exc:  # noqa: BLE001
        logger.error("领取扫码凭据异常：%s", exc)
        return error_response(-1, "扫码登录服务暂不可用，请稍后重试")

    if reason == "not_found":
        return error_response(-1, "会话不存在或已结束")
    if reason == "already_claimed":
        return error_response(-1, "该扫码会话的凭据已被领取，请重新发起扫码")
    if reason != "ok" or data is None:
        return error_response(-1, "扫码尚未完成确认，暂不能领取凭据")
    logger.info("扫码凭据已交付落库（shop_id=%s）", data.get("shop_id"))
    return success_response(data=data, message="凭据领取成功")


@router.post(
    "/qr-login/sessions/{session_id}/cancel",
    response_model=ApiResponse,
    summary="取消扫码会话（服务间调用形态）",
)
async def cancel_qr_session_post(session_id: str, payload: QrSessionClaimRequest) -> ApiResponse:
    """取消扫码会话的 POST 形态。

    与下方 ``DELETE`` 完全同义，仅为适配共通服务间客户端 ``service_client`` 目前
    只导出 ``get_json`` / ``post_json`` 而没有 DELETE 封装这一事实（规范 36：
    不为一个接口去扩张共通层）。backend 走本路径，对外浏览器仍走 DELETE。

    Args:
        session_id: 会话标识。
        payload: 含 owner_user_id 的请求体。

    Returns:
        统一响应体，语义与 ``DELETE`` 一致。
    """
    return await cancel_qr_session(session_id, owner_user_id=payload.owner_user_id)


@router.delete("/qr-login/sessions/{session_id}", response_model=ApiResponse, summary="取消扫码会话")
async def cancel_qr_session(
    session_id: str,
    owner_user_id: int = Query(..., description="发起用户 ID，须与会话归属一致"),
) -> ApiResponse:
    """取消扫码会话并释放其浏览器资源与临时二维码。

    Args:
        session_id: 会话标识。
        owner_user_id: 归属用户 ID。

    Returns:
        统一响应体：取消成功返回成功；会话已是终态返回「会话已结束」。
    """
    try:
        ok, reason = await qr_session_store.cancel_session(session_id, owner_user_id)
    except Exception as exc:  # noqa: BLE001
        logger.error("取消扫码会话异常：%s", exc)
        return error_response(-1, "扫码登录服务暂不可用，请稍后重试")

    if not ok:
        if reason == "terminal":
            return error_response(-1, "会话已结束，无需取消")
        return error_response(-1, "会话不存在或已结束")
    return success_response(
        data={"session_id": session_id, "state": QrOutcome.CANCELLED},
        message="扫码会话已取消",
    )


__all__ = ["router"]


class ViewInput(BaseModel):
    owner_user_id: int
    kind: str
    x: Optional[float] = None
    y: Optional[float] = None
    key: Optional[str] = None
    text: Optional[SecretStr] = Field(None, max_length=256)
    delta_x: Optional[float] = None
    delta_y: Optional[float] = None


@router.get("/qr-login/sessions/{session_id}/frame", response_model=ApiResponse)
async def view_frame(session_id: str, owner_user_id: int = Query(...)):
    try:
        data = await qr_view.frame(session_id, owner_user_id)
    except Exception:
        return error_response(-1, "验证窗口暂不可用，请等待下一帧")
    return success_response(data=data)


@router.post("/qr-login/sessions/{session_id}/input", response_model=ApiResponse)
async def view_input(session_id: str, payload: ViewInput):
    try:
        event = payload.model_dump()
        if payload.text is not None:
            event["text"] = payload.text.get_secret_value()
        ok = await qr_view.input_event(session_id, payload.owner_user_id, event)
    except Exception:
        ok = False
    return success_response() if ok else error_response(-1, "验证窗口已结束或操作无效")


class VerificationAction(BaseModel):
    owner_user_id: int
    action: Literal["submit_sms", "resend_sms"]
    code: Optional[SecretStr] = Field(None, max_length=8)


@router.post("/qr-login/sessions/{session_id}/verification", response_model=ApiResponse)
async def verification_action(session_id: str, payload: VerificationAction):
    from login.password_verification import perform
    try:
        ok, message = await perform(session_id, payload.owner_user_id, payload.action,
                                    payload.code.get_secret_value() if payload.code else None)
    except Exception:
        ok, message = False, "短信验证操作未完成，请等待状态更新后重试"
    return success_response(message=message) if ok else error_response(-1, message)
