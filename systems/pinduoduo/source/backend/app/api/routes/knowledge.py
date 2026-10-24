# -*- coding: utf-8 -*-
"""
backend.app.api.routes.knowledge —— 知识库管理接口路由
======================================================
本文件用途：提供 backend 服务的「商品知识库」与「客服知识库」REST 接口，满足
需求 9（商品知识库管理）与需求 10（客服知识库管理）中的「配置管理」部分：

商品知识（资源键 ``product_knowledge``）：
- ``POST   /product-knowledge``               新增 / upsert 商品知识（需求 9.1/9.2）。
- ``GET    /product-knowledge``               列表（后端分页，需求 9.3）。
- ``GET    /product-knowledge/{item_id}``     查询单条。
- ``PUT    /product-knowledge/{item_id}``     修改。
- ``PUT    /product-knowledge/{item_id}/status`` 启停用。
- ``DELETE /product-knowledge/{item_id}``     逻辑删除（需求 9.5）。

客服知识（资源键 ``cs_knowledge``）：
- ``POST   /cs-knowledge``                    新增（需求 10.1）。
- ``POST   /cs-knowledge/import``             批量导入去重（需求 10.2）。
- ``GET    /cs-knowledge``                    列表（后端分页，需求 10.6）。
- ``GET    /cs-knowledge/{item_id}``          查询单条。
- ``PUT    /cs-knowledge/{item_id}``          修改。
- ``PUT    /cs-knowledge/{item_id}/status``   启停用。
- ``DELETE /cs-knowledge/{item_id}``          逻辑删除。

权限控制（需求 2.4）：所有接口先经统一权限模块 ``permission.check`` 判断当前用户
对对应资源的操作是否被授权；未授权返回 success=false、message「无访问权限」的
统一响应体（HTTP 恒 200）。数据范围隔离（需求 3.7）在 service 层依据当前用户身份
进一步约束「仅可操作本人 / 被授权店铺下的知识」。

接口约定（开发规范 1-3）：HTTP 恒 200，业务成败由统一响应体表达；业务逻辑委托
service 层，路由层仅负责入参解析、鉴权依赖与权限判断；导入置顶、中文注释、全中文。
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core import permission
from app.core.business_codes import CODE_FORBIDDEN, MSG_FORBIDDEN
from app.services import cs_knowledge_service, product_knowledge_service
from common.db.session import get_db
from common.models.user_models import SysUser
from common.schemas.common import ApiResponse, error_response

# 知识库管理路由：标签「知识库」便于 OpenAPI 文档分组；前缀由聚合层添加。
router = APIRouter(tags=["知识库"])

# 受保护资源键约定（与 sys_permission.resource_key 对齐）。
RESOURCE_PRODUCT_KNOWLEDGE: str = "product_knowledge"
RESOURCE_CS_KNOWLEDGE: str = "cs_knowledge"


def _ensure_permission(
    user: SysUser,
    resource_key: str,
    action: str,
    session: Session,
) -> Optional[ApiResponse]:
    """统一权限校验：未授权时返回「无访问权限」响应，授权时返回 None。

    依据需求 2.4，集中经 ``permission.check`` 判断；未授权返回统一失败响应体。

    Args:
        user: 当前登录用户。
        resource_key: 受保护资源键。
        action: 操作（view / create / update / disable）。
        session: 数据库会话。

    Returns:
        未授权时返回失败响应体；已授权返回 None。
    """
    if permission.check(user, resource_key, action, session=session):
        return None
    return error_response(CODE_FORBIDDEN, MSG_FORBIDDEN)


# ======================================================================
# 商品知识请求体模型
# ======================================================================
class UpsertProductKnowledgeRequest(BaseModel):
    """新增 / upsert 商品知识请求体（按 (shop_pk, goods_id) 幂等）。"""

    shop_pk: int = Field(..., description="关联店铺主键 shop.id")
    goods_id: str = Field(..., description="拼多多商品业务标识（业务键）")
    goods_name: Optional[str] = Field(None, description="商品名称")
    price: Optional[float] = Field(None, description="价格")
    price_min: Optional[float] = Field(None, description="最低价")
    price_max: Optional[float] = Field(None, description="最高价")
    sold_quantity: Optional[int] = Field(None, description="已售数量")
    thumb_url: Optional[str] = Field(None, description="缩略图 URL")
    specifications: Optional[str] = Field(None, description="商品规格（JSON 文本）")
    extracted_content: Optional[str] = Field(None, description="抽取内容（供 AI 检索）")


class UpdateProductKnowledgeRequest(BaseModel):
    """修改商品知识请求体（仅更新显式提供的字段）。"""

    goods_name: Optional[str] = Field(None, description="商品名称")
    price: Optional[float] = Field(None, description="价格")
    price_min: Optional[float] = Field(None, description="最低价")
    price_max: Optional[float] = Field(None, description="最高价")
    sold_quantity: Optional[int] = Field(None, description="已售数量")
    thumb_url: Optional[str] = Field(None, description="缩略图 URL")
    specifications: Optional[str] = Field(None, description="商品规格（JSON 文本）")
    extracted_content: Optional[str] = Field(None, description="抽取内容")


class ProductKnowledgeStatusRequest(BaseModel):
    """商品知识启停用请求体。"""

    status: int = Field(..., description="状态：1=启用，0=停用")


# ======================================================================
# 商品知识接口
# ======================================================================
@router.post(
    "/product-knowledge",
    response_model=ApiResponse,
    summary="新增 / 更新商品知识（upsert 幂等）",
)
def upsert_product_knowledge(
    payload: UpsertProductKnowledgeRequest,
    current_user: SysUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """新增 / upsert 商品知识（需求 9.1 / 9.2）。"""
    denied = _ensure_permission(current_user, RESOURCE_PRODUCT_KNOWLEDGE, "create", db)
    if denied is not None:
        return denied
    return product_knowledge_service.upsert_product_knowledge(
        db,
        current_user,
        shop_pk=payload.shop_pk,
        goods_id=payload.goods_id,
        goods_name=payload.goods_name,
        price=payload.price,
        price_min=payload.price_min,
        price_max=payload.price_max,
        sold_quantity=payload.sold_quantity,
        thumb_url=payload.thumb_url,
        specifications=payload.specifications,
        extracted_content=payload.extracted_content,
    )


@router.get(
    "/product-knowledge",
    response_model=ApiResponse,
    summary="商品知识列表（后端分页）",
)
def list_product_knowledge(
    shop_pk: int = Query(..., description="按店铺主键筛选（必填）"),
    page: int = Query(1, description="页码（从 1 开始）"),
    page_size: int = Query(20, description="每页条数（10/20/50/100）"),
    goods_id: Optional[str] = Query(None, description="按商品标识筛选"),
    status: Optional[int] = Query(None, description="按状态筛选：1=启用，0=停用"),
    current_user: SysUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """分页查询商品知识列表（需求 9.3，后端分页）。"""
    denied = _ensure_permission(current_user, RESOURCE_PRODUCT_KNOWLEDGE, "view", db)
    if denied is not None:
        return denied
    return product_knowledge_service.list_product_knowledge(
        db,
        current_user,
        shop_pk=shop_pk,
        page=page,
        page_size=page_size,
        goods_id=goods_id,
        status=status,
    )


@router.get(
    "/product-knowledge/{item_id}",
    response_model=ApiResponse,
    summary="查询单条商品知识",
)
def get_product_knowledge(
    item_id: int,
    current_user: SysUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """查询单条商品知识。"""
    denied = _ensure_permission(current_user, RESOURCE_PRODUCT_KNOWLEDGE, "view", db)
    if denied is not None:
        return denied
    return product_knowledge_service.get_product_knowledge(db, current_user, item_id)


@router.put(
    "/product-knowledge/{item_id}",
    response_model=ApiResponse,
    summary="修改商品知识",
)
def update_product_knowledge(
    item_id: int,
    payload: UpdateProductKnowledgeRequest,
    current_user: SysUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """修改商品知识字段（需求 9.1 配套）。"""
    denied = _ensure_permission(current_user, RESOURCE_PRODUCT_KNOWLEDGE, "update", db)
    if denied is not None:
        return denied
    return product_knowledge_service.update_product_knowledge(
        db,
        current_user,
        item_id,
        goods_name=payload.goods_name,
        price=payload.price,
        price_min=payload.price_min,
        price_max=payload.price_max,
        sold_quantity=payload.sold_quantity,
        thumb_url=payload.thumb_url,
        specifications=payload.specifications,
        extracted_content=payload.extracted_content,
    )


@router.put(
    "/product-knowledge/{item_id}/status",
    response_model=ApiResponse,
    summary="启用 / 停用商品知识",
)
def set_product_knowledge_status(
    item_id: int,
    payload: ProductKnowledgeStatusRequest,
    current_user: SysUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """启用或停用商品知识。"""
    action = "update" if int(payload.status) == 1 else "disable"
    denied = _ensure_permission(current_user, RESOURCE_PRODUCT_KNOWLEDGE, action, db)
    if denied is not None:
        return denied
    return product_knowledge_service.set_product_knowledge_status(
        db, current_user, item_id, payload.status
    )


@router.delete(
    "/product-knowledge/{item_id}",
    response_model=ApiResponse,
    summary="删除商品知识（逻辑删除）",
)
def delete_product_knowledge(
    item_id: int,
    current_user: SysUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """逻辑删除商品知识（需求 9.5，规范 11：禁止物理删除）。"""
    denied = _ensure_permission(current_user, RESOURCE_PRODUCT_KNOWLEDGE, "disable", db)
    if denied is not None:
        return denied
    return product_knowledge_service.delete_product_knowledge(db, current_user, item_id)


# ======================================================================
# 客服知识请求体模型
# ======================================================================
class CreateCsKnowledgeRequest(BaseModel):
    """新增客服知识请求体（需求 10.1）。"""

    shop_pk: int = Field(..., description="关联店铺主键 shop.id")
    title: str = Field(..., description="知识标题")
    content: str = Field(..., description="知识内容")
    tags: Optional[str] = Field(None, description="标签（逗号分隔）")
    enabled: bool = Field(True, description="是否启用")


class UpdateCsKnowledgeRequest(BaseModel):
    """修改客服知识请求体（仅更新显式提供的字段）。"""

    title: Optional[str] = Field(None, description="知识标题")
    content: Optional[str] = Field(None, description="知识内容")
    tags: Optional[str] = Field(None, description="标签（逗号分隔）")
    enabled: Optional[bool] = Field(None, description="是否启用")


class CsKnowledgeStatusRequest(BaseModel):
    """客服知识启停用请求体。"""

    enabled: bool = Field(..., description="True=启用，False=停用")


class CsKnowledgeImportItem(BaseModel):
    """批量导入的单条客服知识。"""

    title: str = Field(..., description="知识标题")
    content: str = Field(..., description="知识内容")
    tags: Optional[str] = Field(None, description="标签（逗号分隔）")
    enabled: bool = Field(True, description="是否启用")


class ImportCsKnowledgeRequest(BaseModel):
    """批量导入客服知识请求体（需求 10.2：去重导入）。"""

    shop_pk: int = Field(..., description="关联店铺主键 shop.id（导入项统一归属）")
    items: List[CsKnowledgeImportItem] = Field(..., description="待导入条目列表")


# ======================================================================
# 客服知识接口
# ======================================================================
@router.post("/cs-knowledge", response_model=ApiResponse, summary="新增客服知识")
def create_cs_knowledge(
    payload: CreateCsKnowledgeRequest,
    current_user: SysUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """新增客服知识（需求 10.1）。"""
    denied = _ensure_permission(current_user, RESOURCE_CS_KNOWLEDGE, "create", db)
    if denied is not None:
        return denied
    return cs_knowledge_service.create_cs_knowledge(
        db,
        current_user,
        shop_pk=payload.shop_pk,
        title=payload.title,
        content=payload.content,
        tags=payload.tags,
        enabled=payload.enabled,
    )


@router.post(
    "/cs-knowledge/import",
    response_model=ApiResponse,
    summary="批量导入客服知识（去重）",
)
def import_cs_knowledge(
    payload: ImportCsKnowledgeRequest,
    current_user: SysUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """批量导入客服知识，跳过同店铺内 (标题, 内容) 完全相同的重复项（需求 10.2）。

    返回成功数量与跳过数量。
    """
    denied = _ensure_permission(current_user, RESOURCE_CS_KNOWLEDGE, "create", db)
    if denied is not None:
        return denied
    items = [item.model_dump() for item in payload.items]
    return cs_knowledge_service.import_cs_knowledge(
        db, current_user, shop_pk=payload.shop_pk, items=items
    )


@router.get(
    "/cs-knowledge",
    response_model=ApiResponse,
    summary="客服知识列表（后端分页）",
)
def list_cs_knowledge(
    shop_pk: int = Query(..., description="按店铺主键筛选（必填）"),
    page: int = Query(1, description="页码（从 1 开始）"),
    page_size: int = Query(20, description="每页条数（10/20/50/100）"),
    enabled: Optional[bool] = Query(None, description="按启用状态筛选"),
    current_user: SysUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """分页查询客服知识列表（需求 10.6，后端分页）。"""
    denied = _ensure_permission(current_user, RESOURCE_CS_KNOWLEDGE, "view", db)
    if denied is not None:
        return denied
    return cs_knowledge_service.list_cs_knowledge(
        db,
        current_user,
        shop_pk=shop_pk,
        page=page,
        page_size=page_size,
        enabled=enabled,
    )


@router.get(
    "/cs-knowledge/{item_id}",
    response_model=ApiResponse,
    summary="查询单条客服知识",
)
def get_cs_knowledge(
    item_id: int,
    current_user: SysUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """查询单条客服知识。"""
    denied = _ensure_permission(current_user, RESOURCE_CS_KNOWLEDGE, "view", db)
    if denied is not None:
        return denied
    return cs_knowledge_service.get_cs_knowledge(db, current_user, item_id)


@router.put(
    "/cs-knowledge/{item_id}",
    response_model=ApiResponse,
    summary="修改客服知识",
)
def update_cs_knowledge(
    item_id: int,
    payload: UpdateCsKnowledgeRequest,
    current_user: SysUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """修改客服知识字段（需求 10.1 配套）。"""
    denied = _ensure_permission(current_user, RESOURCE_CS_KNOWLEDGE, "update", db)
    if denied is not None:
        return denied
    return cs_knowledge_service.update_cs_knowledge(
        db,
        current_user,
        item_id,
        title=payload.title,
        content=payload.content,
        tags=payload.tags,
        enabled=payload.enabled,
    )


@router.put(
    "/cs-knowledge/{item_id}/status",
    response_model=ApiResponse,
    summary="启用 / 停用客服知识",
)
def set_cs_knowledge_status(
    item_id: int,
    payload: CsKnowledgeStatusRequest,
    current_user: SysUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """启用或停用客服知识（停用项检索时不返回，需求 10.5）。"""
    action = "update" if payload.enabled else "disable"
    denied = _ensure_permission(current_user, RESOURCE_CS_KNOWLEDGE, action, db)
    if denied is not None:
        return denied
    return cs_knowledge_service.set_cs_knowledge_status(
        db, current_user, item_id, payload.enabled
    )


@router.delete(
    "/cs-knowledge/{item_id}",
    response_model=ApiResponse,
    summary="删除客服知识（逻辑删除）",
)
def delete_cs_knowledge(
    item_id: int,
    current_user: SysUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """逻辑删除客服知识（规范 11：禁止物理删除）。"""
    denied = _ensure_permission(current_user, RESOURCE_CS_KNOWLEDGE, "disable", db)
    if denied is not None:
        return denied
    return cs_knowledge_service.delete_cs_knowledge(db, current_user, item_id)


__all__ = ["router"]
