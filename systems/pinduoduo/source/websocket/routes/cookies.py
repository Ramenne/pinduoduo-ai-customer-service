# -*- coding: utf-8 -*-
"""
websocket.routes.cookies —— Cookie 刷新接口（供 scheduler 调用）
==============================================================
本文件用途：提供 websocket 服务的「Cookie 刷新」HTTP 接口，供 scheduler 定时任务
服务经服务间 HTTP 调用，定时刷新指定店铺账号的登录态 Cookie（需求 4.6 / 21.2）。

- ``POST /cookies/refresh``：按 (shop_id, owner_user_id) 读取数据库权威授权，复用
  ``channel_pdd.session_health.maintain_session`` 验证身份并有界续期；
  刷新成功后将新 Cookie 以可逆加密回写 account 表（不返回明文，需求 3.6）。

接口约定（开发规范 1-3）：HTTP 恒返回 200，业务成败由统一响应体
``{code, success, message, data}`` 表达。地址经环境变量配置（禁止写死 localhost，
规范 21）。本路由不抛异常，失败统一规整为失败响应（健壮性兜底，需求 26）；
对外响应不包含 Cookie 等敏感字段（需求 3.6）。

实现约束（开发规范）：导入置顶（规范 51）、中文注释（规范 37）、文件名用下划线
（规范 40）、单文件 ≤500 行（规范 35）、日志禁用 debug（规范 38）、复用共通（规范 36/52）。
"""
from __future__ import annotations

import logging
import asyncio
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from channel_pdd.session_health import maintain_session
from common.schemas.common import ApiResponse, error_response, success_response

logger = logging.getLogger("websocket.routes.cookies")

# Cookie 刷新路由：标签便于 OpenAPI 分组；前缀由聚合层添加。
router = APIRouter(tags=["Cookie 刷新"])


class RefreshCookieRequest(BaseModel):
    """Cookie 刷新请求体（与 scheduler service_client 约定一致）。"""

    shop_pk: int = Field(..., description="店铺主键 shop.id")
    shop_id: str = Field(..., description="拼多多店铺业务标识")
    owner_user_id: Optional[int] = Field(None, description="店铺归属用户 ID")


@router.post(
    "/cookies/refresh",
    response_model=ApiResponse,
    summary="刷新指定店铺账号的登录态 Cookie",
)
async def refresh_cookie(payload: RefreshCookieRequest) -> ApiResponse:
    """刷新指定店铺账号的登录态 Cookie（需求 4.6 / 21.2）。

    与实时连接使用同一续期器，条件写回保证旧任务不能覆盖新授权。
    明确区分授权过期、人工验证与临时错误；响应不包含 Cookie。

    Args:
        payload: 含 shop_pk / shop_id / owner_user_id 的刷新请求体。

    Returns:
        统一响应体：刷新成功返回 success；失败返回 error_response。
    """
    # 使用同一数据库授权和有界续期路径；不再依赖账号密码登录遗留目录。
    if payload.owner_user_id is None:
        logger.warning("Cookie 刷新缺少归属用户 ID：shop_id=%s", payload.shop_id)
        return error_response(-1, "缺少归属用户信息，无法刷新 Cookie")

    try:
        result = await asyncio.to_thread(maintain_session, payload.shop_id, payload.owner_user_id)
    except Exception:
        logger.error("授权维护暂时失败，保留现有凭据：shop_pk=%s", payload.shop_pk)
        return error_response(-1, "授权维护暂未完成，请稍后重试")
    if result.state == 'online':
        return success_response(data={'shop_pk': payload.shop_pk, 'state': result.state}, message='授权已验证并维护')
    if result.state in ('expired', 'verification_required'):
        return error_response(-1, '平台需要重新授权或人工验证，请在店铺管理处理')
    return error_response(-1, '授权维护暂未完成，已保留当前凭据并等待自动重试')



__all__ = ["router"]
