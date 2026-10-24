# -*- coding: utf-8 -*-
"""
backend.app.api.routes.products —— 商品管理接口路由
===================================================
本文件用途：提供 backend 服务的「商品管理」REST 接口，满足需求 15（商品管理）：

- ``GET    /products``                          商品列表（后端分页，需求 15.1）。
- ``GET    /products/{product_id}/detail``       查看商品详情（库内信息 + 实时规格，需求 15）。
- ``POST   /products/sync``                      触发商品同步（需求 15.2/15.3/15.4）。
- ``POST   /products/{product_id}/goods-reply``  从商品记录创建商品专属回复（需求 15.5）。
- ``POST   /products/{product_id}/knowledge``    从商品记录创建商品知识（需求 15.5）。

商品同步（需求 15.2/15.3）：经 HTTP 调用 websocket 服务从拼多多拉取商品并 upsert
入库（同 goods_id 更新而非新建，需求 15.4）；当 Cookie 缺少有效 anti-content 签名
导致拉取失败时，**终止同步**并返回固定中文提示「当前 Cookie 缺少有效签名，请通过
账号密码登录重新获取后再同步商品」，同时记录系统日志（需求 15.3 / 26.2）。

权限控制（需求 2.4）：所有接口先经统一权限模块 ``permission.check`` 判断当前用户
对资源 ``product`` 的对应操作是否被授权；未授权返回 success=false、message「无访问
权限」的统一响应体（HTTP 恒 200）。数据范围隔离在服务层按店铺归属统一处理（需求 3.7）。

接口约定（开发规范 1-3）：所有接口 HTTP 恒返回 200，业务成败由统一响应体
{code, success, message, data} 表达；业务逻辑委托 app.services.product_service。
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core import permission
from app.core.business_codes import CODE_FORBIDDEN, MSG_FORBIDDEN
from app.services import product_service
from common.db.session import get_db
from common.models.user_models import SysUser
from common.schemas.common import ApiResponse, error_response

# 商品管理路由：标签便于 OpenAPI 文档分组；前缀由聚合层添加。
router = APIRouter(tags=["商品管理"])

# 受保护资源键约定（与 sys_permission.resource_key 对齐）。
RESOURCE_PRODUCT: str = "product"


def _ensure_permission(
    user: SysUser, action: str, session: Session
) -> Optional[ApiResponse]:
    """统一权限校验：未授权时返回「无访问权限」响应，授权时返回 None。

    Args:
        user: 当前登录用户。
        action: 操作（view / create / update / sync）。
        session: 数据库会话。

    Returns:
        未授权时返回失败响应体；已授权返回 None。
    """
    if permission.check(user, RESOURCE_PRODUCT, action, session=session):
        return None
    return error_response(CODE_FORBIDDEN, MSG_FORBIDDEN)


# ----------------------------------------------------------------------
# 请求体模型
# ----------------------------------------------------------------------
class SyncProductsRequest(BaseModel):
    """触发商品同步请求体。"""

    shop_pk: int = Field(..., description="店铺主键 shop.id")


class CreateGoodsReplyFromProductRequest(BaseModel):
    """从商品记录创建商品专属回复请求体。"""

    reply_content: str = Field(..., description="回复内容")
    reply_type: str = Field("text", description="回复类型：text/image")
    enabled: bool = Field(True, description="是否启用")


class CreateKnowledgeFromProductRequest(BaseModel):
    """从商品记录创建商品知识请求体。"""

    extracted_content: Optional[str] = Field(None, description="供 AI 检索的抽取内容")
    specifications: Optional[str] = Field(None, description="商品规格（JSON 文本）")


# ----------------------------------------------------------------------
# 商品列表接口（需求 15.1）
# ----------------------------------------------------------------------
@router.get("/products", response_model=ApiResponse, summary="商品列表（后端分页）")
def list_products(
    shop_pk: int = Query(..., description="店铺主键 shop.id"),
    page: int = Query(1, description="页码（从 1 开始）"),
    page_size: int = Query(20, description="每页条数（10/20/50/100）"),
    status: Optional[int] = Query(None, description="按状态筛选：1=启用，0=停用"),
    current_user: SysUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """分页查询某店铺的商品列表（需求 15.1）。"""
    denied = _ensure_permission(current_user, "view", db)
    if denied is not None:
        return denied
    return product_service.list_products(
        db,
        current_user,
        shop_pk=shop_pk,
        page=page,
        page_size=page_size,
        status=status,
    )


# ----------------------------------------------------------------------
# 商品详情接口（需求 15）
# ----------------------------------------------------------------------
@router.get(
    "/products/{product_id}/detail",
    response_model=ApiResponse,
    summary="查看商品详情（默认读库，显式刷新规格）",
)
def get_product_detail(
    product_id: int,
    refresh_specs: bool = Query(False, description="显式补充实时规格；默认仅查询已保存数据"),
    current_user: SysUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """查看商品已有信息；refresh_specs=true 时才查询实时规格。"""
    denied = _ensure_permission(current_user, "view", db)
    if denied is not None:
        return denied
    if refresh_specs:
        denied = _ensure_permission(current_user, "update", db)
        if denied is not None:
            return denied
    return product_service.get_product_detail(db, current_user, product_id=product_id, refresh_specs=refresh_specs)


# ----------------------------------------------------------------------
# 触发商品同步接口（需求 15.2/15.3/15.4）
# ----------------------------------------------------------------------
@router.post("/products/sync", response_model=ApiResponse, summary="触发商品同步")
def sync_products(
    payload: SyncProductsRequest,
    current_user: SysUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """触发商品同步：拉取拼多多商品并 upsert 入库；签名缺失时降级提示（需求 15.2/15.3）。"""
    denied = _ensure_permission(current_user, "update", db)
    if denied is not None:
        return denied
    return product_service.sync_products(db, current_user, shop_pk=payload.shop_pk)


# ----------------------------------------------------------------------
# 从商品记录创建商品专属回复 / 商品知识（需求 15.5）
# ----------------------------------------------------------------------
@router.post(
    "/products/{product_id}/goods-reply",
    response_model=ApiResponse,
    summary="从商品记录创建商品专属回复",
)
def create_goods_reply_from_product(
    product_id: int,
    payload: CreateGoodsReplyFromProductRequest,
    current_user: SysUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """从商品记录创建商品专属回复（关联 goods_id 并持久化，需求 15.5）。"""
    denied = _ensure_permission(current_user, "create", db)
    if denied is not None:
        return denied
    return product_service.create_goods_reply_from_product(
        db,
        current_user,
        product_id=product_id,
        reply_content=payload.reply_content,
        reply_type=payload.reply_type,
        enabled=payload.enabled,
    )


@router.post(
    "/products/{product_id}/knowledge",
    response_model=ApiResponse,
    summary="从商品记录创建商品知识",
)
def create_product_knowledge_from_product(
    product_id: int,
    payload: CreateKnowledgeFromProductRequest,
    current_user: SysUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """从商品记录创建 / 更新商品知识（关联 goods_id 并持久化，需求 15.5）。"""
    denied = _ensure_permission(current_user, "create", db)
    if denied is not None:
        return denied
    return product_service.create_product_knowledge_from_product(
        db,
        current_user,
        product_id=product_id,
        extracted_content=payload.extracted_content,
        specifications=payload.specifications,
    )


__all__ = ["router"]
