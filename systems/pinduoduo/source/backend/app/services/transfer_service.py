# -*- coding: utf-8 -*-
"""
backend.app.services.transfer_service —— 转人工设置业务服务（需求 16.1）
======================================================================
本文件用途：实现 backend 服务的「转人工设置」业务逻辑，供 transfer 路由复用，满足
需求 16.1（转人工设置）：

- ``list_cs_list(...)``：查询某店铺可分配的人工客服列表（客服标识与名称）。客服
  列表依赖店铺 Cookie 实时调用拼多多接口，由 websocket 服务承载，本服务据 shop_pk
  解析出 shop_id / owner_user_id 后经 ``transfer_client`` HTTP 调 websocket 获取。
- ``list_transfer_keywords(...)``：转人工关键词后端分页列表，可按店铺 / 启用筛选。
- ``create_transfer_keyword(...)``：新增转人工关键词（命中后暂停自动回复并转人工）。
- ``set_transfer_keyword_status(...)``：启用 / 停用转人工关键词。

实现约束（开发规范）：
- 统一响应体由 common.schemas.common 构造，HTTP 恒 200（规范 1-3）。
- 所有数据访问经 common.db.repository 参数化查询，禁止拼接 SQL（规范 16）。
- 时间字段为北京时间（规范 17）；导入置顶（规范 51）；中文注释（规范 37）；
  单文件 ≤500 行（规范 35）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.core.business_codes import CODE_NOT_FOUND, CODE_PARAM_ERROR
from app.core.data_scope import ensure_shop_in_scope, paginate_shop_scoped
from app.services import transfer_client
from common.db.repository import Repository
from common.models.config_models import TransferKeyword
from common.models.shop_models import Shop
from common.schemas.common import ApiResponse, error_response, success_response


# ----------------------------------------------------------------------
# 序列化辅助
# ----------------------------------------------------------------------
def serialize_transfer_keyword(row: TransferKeyword) -> Dict[str, Any]:
    """将转人工关键词模型序列化为对外字典（时间为北京时间）。

    Args:
        row: 转人工关键词模型实例。

    Returns:
        转人工关键词信息字典。
    """
    return {
        "id": row.id,
        "shop_pk": row.shop_pk,
        "keyword": row.keyword,
        "enabled": bool(row.enabled),
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


# ----------------------------------------------------------------------
# 客服列表（需求 16.1）
# ----------------------------------------------------------------------
def list_cs_list(
    session: Session, shop_pk: int, *, operator_id: Optional[int] = None
) -> ApiResponse:
    """查询某店铺可分配的人工客服列表（需求 16.1）。

    据 shop_pk 定位店铺，取出 shop_id / owner_user_id 后经 HTTP 调 websocket 服务
    实时获取客服列表；websocket 不可达 / 查询失败时降级返回空列表并附中文提示，
    不整体失败（健壮性兜底，需求 26）。非管理员仅可查看其可见范围内店铺（需求 3.7）。

    Args:
        session: 数据库会话。
        shop_pk: 店铺主键 shop.id。
        operator_id: 当前操作用户 ID（数据范围校验）。

    Returns:
        统一响应体：data={list: 客服列表, message: 提示}。
    """
    # 数据范围隔离：店铺需存在且在当前用户可见范围内（需求 3.7 / 规范 42a）。
    denied = ensure_shop_in_scope(session, shop_pk, operator_id)
    if denied is not None:
        return denied

    shop = Repository(Shop, session).get(shop_pk)
    result = transfer_client.fetch_cs_list(
        shop_id=shop.shop_id, owner_user_id=shop.owner_user_id
    )
    data = {
        "list": result.cs_list,
        "message": "" if result.ok else (result.message or "客服列表暂不可用"),
    }
    return success_response(data=data, message="查询成功")


# ----------------------------------------------------------------------
# 转人工关键词列表（后端分页）
# ----------------------------------------------------------------------
def list_transfer_keywords(
    session: Session,
    page: Any = 1,
    page_size: Any = 20,
    shop_pk: Optional[int] = None,
    enabled: Optional[bool] = None,
    *,
    operator_id: Optional[int] = None,
) -> ApiResponse:
    """分页查询转人工关键词列表（后端分页，需求 16.1）。

    默认按创建时间倒序（仓储层自动探测时间字段）。可按店铺与启用状态筛选。
    非管理员仅可见其可访问店铺下的关键词（数据范围隔离，需求 3.7）。

    Args:
        session: 数据库会话。
        page: 页码（从 1 开始，将被规整）。
        page_size: 每页条数（10/20/50/100，将被规整）。
        shop_pk: 按店铺主键筛选；None 表示该用户可见范围内全部店铺。
        enabled: 按启用状态筛选；None 表示不筛选。
        operator_id: 当前操作用户 ID（数据范围校验）。

    Returns:
        统一响应体：data 为分页结构 {list, total, page, page_size}。
    """
    return paginate_shop_scoped(
        session,
        model=TransferKeyword,
        serializer=serialize_transfer_keyword,
        page=page,
        page_size=page_size,
        operator_id=operator_id,
        shop_pk=int(shop_pk) if shop_pk is not None else None,
        extra_filters={"enabled": bool(enabled)} if enabled is not None else None,
    )


# ----------------------------------------------------------------------
# 新增转人工关键词
# ----------------------------------------------------------------------
def create_transfer_keyword(
    session: Session,
    shop_pk: int,
    keyword: str,
    *,
    enabled: bool = True,
    operator_id: Optional[int] = None,
) -> ApiResponse:
    """新增转人工关键词并持久化（需求 16.1 / 16.3）。

    同一店铺下关键词去重：已存在相同关键词时，复用原记录并置为启用（避免重复新建）。

    Args:
        session: 数据库会话。
        shop_pk: 关联店铺主键 shop.id。
        keyword: 转人工关键词。
        enabled: 是否启用，默认 True。
        operator_id: 操作人用户 ID（创建人审计字段）。

    Returns:
        统一响应体：成功返回关键词信息；失败返回中文提示。
    """
    if shop_pk is None or int(shop_pk) <= 0:
        return error_response(CODE_PARAM_ERROR, "店铺标识非法")
    if not keyword or not keyword.strip():
        return error_response(CODE_PARAM_ERROR, "转人工关键词不能为空")

    # 数据范围隔离：店铺需存在且在当前用户可见范围内（需求 3.7 / 规范 42a）。
    denied = ensure_shop_in_scope(session, int(shop_pk), operator_id)
    if denied is not None:
        return denied

    keyword = keyword.strip()
    repo = Repository(TransferKeyword, session)

    # 同店铺同关键词去重：已存在则复用并启用，保证幂等不重复（关系由代码维护）。
    existing = repo.get_by(shop_pk=int(shop_pk), keyword=keyword)
    if existing is not None:
        repo.update(existing.id, enabled=bool(enabled))
        return success_response(
            data=serialize_transfer_keyword(existing), message="保存成功"
        )

    row = repo.create(
        shop_pk=int(shop_pk),
        keyword=keyword,
        enabled=bool(enabled),
        created_by=operator_id,
    )
    return success_response(data=serialize_transfer_keyword(row), message="创建成功")


# ----------------------------------------------------------------------
# 启停用（下一条消息生效）
# ----------------------------------------------------------------------
def set_transfer_keyword_status(
    session: Session,
    keyword_id: int,
    enabled: bool,
    *,
    operator_id: Optional[int] = None,
) -> ApiResponse:
    """启用或停用转人工关键词（需求 16.1）。

    停用经状态字段 ``enabled=False`` 标记，停用关键词不参与转人工判定。该变更在
    下一条消息处理时由 websocket 引擎实时读取生效，无需额外刷新。非管理员仅可
    操作其可见范围内店铺的关键词（数据范围隔离，需求 3.7）。

    Args:
        session: 数据库会话。
        keyword_id: 目标关键词 ID。
        enabled: True=启用，False=停用。
        operator_id: 当前操作用户 ID（数据范围校验）。

    Returns:
        统一响应体：成功返回更新后的关键词信息。
    """
    repo = Repository(TransferKeyword, session)
    row = repo.get(keyword_id)
    if row is None:
        return error_response(CODE_NOT_FOUND, "目标转人工关键词不存在")

    # 数据范围隔离：关键词所属店铺需在当前用户可见范围内（需求 3.7 / 规范 42a）。
    denied = ensure_shop_in_scope(session, row.shop_pk, operator_id)
    if denied is not None:
        return denied

    repo.update(keyword_id, enabled=bool(enabled))
    message = "已启用" if enabled else "已停用"
    return success_response(data=serialize_transfer_keyword(row), message=message)


__all__ = [
    "serialize_transfer_keyword",
    "list_cs_list",
    "list_transfer_keywords",
    "create_transfer_keyword",
    "set_transfer_keyword_status",
]
