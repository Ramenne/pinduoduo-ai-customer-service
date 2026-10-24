# -*- coding: utf-8 -*-
"""
backend.app.api.routes.ai_config —— AI（LLM）配置接口路由
========================================================
本文件用途：提供 backend 服务的「AI 智能回复配置」REST 接口，满足需求 8
（AI 智能回复配置，支持选择接口类型 + 测试连接）：

- ``GET  /shops/{shop_pk}/ai-config``        查询某店铺 AI 配置（密钥不返回明文）。
- ``PUT  /shops/{shop_pk}/ai-config``        保存（upsert）AI 配置（密钥加密存储）。
- ``POST /shops/{shop_pk}/ai-config/test``   按所选接口类型测试 AI 连接。
- ``GET  /ai-provider-types``                查询 AI 接口类型枚举（key->中文，规范 15）。

权限控制（需求 2.4）：所有接口先经统一权限模块 ``permission.check`` 判断当前
用户对资源 ``reply``（自动回复设置）的对应操作是否被授权；未授权返回
success=false、message「无访问权限」的统一响应体（HTTP 恒 200）。

接口约定（开发规范 1-3）：HTTP 恒返回 200，业务成败由统一响应体
{code, success, message, data} 表达；业务逻辑委托 app.services.ai_config_service。
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core import permission
from app.core.business_codes import CODE_FORBIDDEN, MSG_FORBIDDEN
from app.services import ai_config_service
from common.db.session import get_db
from common.models.user_models import SysUser
from common.schemas.common import ApiResponse, error_response

# AI 配置路由：标签便于 OpenAPI 文档分组；前缀由聚合层添加。
router = APIRouter(tags=["AI 设置"])

# 受保护资源键：AI 配置为「店铺级设置」，统一归属店铺管理（shop）资源判权——
# 入口收敛到店铺管理页后，有店铺管理权限即可操作店铺级设置（与前端入口一致）。
RESOURCE_REPLY: str = "shop"


def _ensure_permission(
    user: SysUser, action: str, session: Session
) -> Optional[ApiResponse]:
    """统一权限校验：未授权时返回「无访问权限」响应，授权时返回 None。

    Args:
        user: 当前登录用户。
        action: 操作（view / update）。
        session: 数据库会话。

    Returns:
        未授权时返回失败响应体；已授权返回 None。
    """
    if permission.check(user, RESOURCE_REPLY, action, session=session):
        return None
    return error_response(CODE_FORBIDDEN, MSG_FORBIDDEN)


# ----------------------------------------------------------------------
# 请求体模型
# ----------------------------------------------------------------------
class SaveAiConfigRequest(BaseModel):
    """保存 AI 配置请求体（需求 8.6）。"""

    provider_type: Optional[str] = Field(
        None, description="接口类型：openai_compatible/anthropic/gemini/dashscope_app"
    )
    model_name: Optional[str] = Field(None, description="模型名称")
    api_base: Optional[str] = Field(None, description="API 接口地址")
    instructions: Optional[str] = Field(None, description="提示词指令")
    api_key: Optional[str] = Field(None, description="API 密钥（留空表示不修改已存密钥）")
    ai_enabled: bool = Field(False, description="是否启用 AI 回复")


class TestAiConfigRequest(BaseModel):
    """测试 AI 连接请求体（密钥留空时回退使用已存密钥）。"""

    provider_type: Optional[str] = Field(None, description="接口类型")
    model_name: Optional[str] = Field(None, description="模型名称")
    api_base: Optional[str] = Field(None, description="API 接口地址")
    api_key: Optional[str] = Field(None, description="API 密钥（留空则用已保存的密钥测试）")


class FetchModelsRequest(BaseModel):
    """获取 AI 模型列表请求体（密钥留空时回退使用已存密钥）。"""

    provider_type: Optional[str] = Field(None, description="接口类型")
    api_base: Optional[str] = Field(None, description="API 接口地址")
    api_key: Optional[str] = Field(None, description="API 密钥（留空则用已保存的密钥）")


# ----------------------------------------------------------------------
# 接口
# ----------------------------------------------------------------------
@router.get("/ai-provider-types", response_model=ApiResponse, summary="查询 AI 接口类型枚举")
def list_provider_types(
    current_user: SysUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """查询 AI 接口类型枚举（key->中文 + 默认地址，规范 15）。"""
    denied = _ensure_permission(current_user, "view", db)
    if denied is not None:
        return denied
    return ai_config_service.list_provider_types(db)


@router.get(
    "/shops/{shop_pk}/ai-config",
    response_model=ApiResponse,
    summary="查询店铺 AI 配置",
)
def get_ai_config(
    shop_pk: int,
    current_user: SysUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """查询某店铺的 AI 配置（API 密钥不返回明文，需求 8.6）。"""
    denied = _ensure_permission(current_user, "view", db)
    if denied is not None:
        return denied
    return ai_config_service.get_ai_config(db, shop_pk, operator_id=current_user.id)


@router.put(
    "/shops/{shop_pk}/ai-config",
    response_model=ApiResponse,
    summary="保存店铺 AI 配置",
)
def save_ai_config(
    shop_pk: int,
    payload: SaveAiConfigRequest,
    current_user: SysUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """保存（upsert）某店铺 AI 配置；API 密钥加密存储，留空表示不修改（需求 8.6）。"""
    denied = _ensure_permission(current_user, "update", db)
    if denied is not None:
        return denied
    return ai_config_service.save_ai_config(
        db,
        shop_pk,
        provider_type=payload.provider_type,
        model_name=payload.model_name,
        api_base=payload.api_base,
        instructions=payload.instructions,
        api_key=payload.api_key,
        ai_enabled=payload.ai_enabled,
        operator_id=current_user.id,
    )


@router.post(
    "/shops/{shop_pk}/ai-config/test",
    response_model=ApiResponse,
    summary="测试店铺 AI 连接",
)
def test_ai_config(
    shop_pk: int,
    payload: TestAiConfigRequest,
    current_user: SysUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """按所选接口类型测试 AI 连接（需求 8 测试连接）；密钥留空则用已保存密钥测试。"""
    denied = _ensure_permission(current_user, "update", db)
    if denied is not None:
        return denied
    return ai_config_service.test_ai_config(
        db,
        shop_pk,
        provider_type=payload.provider_type,
        model_name=payload.model_name,
        api_base=payload.api_base,
        api_key=payload.api_key,
        operator_id=current_user.id,
    )


@router.post(
    "/shops/{shop_pk}/ai-config/models",
    response_model=ApiResponse,
    summary="获取店铺 AI 可用模型列表",
)
def fetch_ai_models(
    shop_pk: int,
    payload: FetchModelsRequest,
    current_user: SysUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """按所选接口类型从服务商拉取可用模型列表（密钥留空则用已保存密钥）。

    获取失败 / 服务商不支持时返回 success=false 与中文原因，前端据此改为手动填写。
    """
    denied = _ensure_permission(current_user, "view", db)
    if denied is not None:
        return denied
    return ai_config_service.fetch_models(
        db,
        shop_pk,
        provider_type=payload.provider_type,
        api_base=payload.api_base,
        api_key=payload.api_key,
        operator_id=current_user.id,
    )


__all__ = ["router"]
