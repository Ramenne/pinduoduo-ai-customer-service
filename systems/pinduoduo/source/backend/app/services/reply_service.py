# -*- coding: utf-8 -*-
"""
backend.app.services.reply_service —— 默认回复与商品专属回复业务服务
====================================================================
本文件用途：实现 backend 服务的「默认回复」与「商品专属回复」业务逻辑，供
replies 路由复用，满足需求 7（默认回复与商品专属回复）：

- 默认回复（default_reply 表，按店铺维度一条配置）：
  * ``get_default_reply(...)``：查询某店铺的默认回复配置（需求 7.1 配套）。
  * ``save_default_reply(...)``：保存（upsert）某店铺的默认回复内容与启停用，
    同 ``shop_pk`` 幂等更新而非新建（需求 7.1）。
  * ``set_default_reply_status(...)``：启用 / 停用默认回复。
- 商品专属回复（goods_reply 表，绑定 goods_id，优先级高于默认，需求 7.2/7.4）：
  * ``create_goods_reply(...)``：新增 / upsert 商品专属回复（绑定 goods_id，
    同 (shop_pk, goods_id) 幂等更新，需求 7.3）。
  * ``update_goods_reply(...)``：按主键更新商品专属回复。
  * ``set_goods_reply_status(...)`` / ``delete_goods_reply(...)``：启停用与
    逻辑删除（禁止物理删除，需求 24.6）。
  * ``list_goods_replies(...)``：后端分页查询商品专属回复列表（需求 7.5）。

说明：需求 7.1/7.2/7.4 中「未命中关键词时返回默认回复」「商品专属优先于默认」
等运行时决策由 websocket 服务的自动回复引擎（任务 12.8）执行，本文件只负责
backend 侧的配置持久化、查询与分页（CRUD）。

实现约束（开发规范）：
- 统一响应体由 common.schemas.common 构造，HTTP 恒 200（规范 1-3）。
- 所有数据访问经 common.db.repository 的参数化查询，禁止拼接 SQL（规范 16）。
- 数据范围隔离复用 app.core.data_scope：非管理员仅能操作本人 / 被授权店铺下的
  回复配置（需求 3.7 / Property 8）。
- 禁止物理删除业务数据，停用 / 删除经状态字段实现（规范 11 / 需求 24.6）。
- 回复类型 reply_type（text/image）枚举入 sys_dict，前端查中文展示（需求 24.7）。
- 导入置顶（规范 51）；中文注释（规范 37）；单文件 ≤500 行（规范 35）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.core.business_codes import (
    CODE_FORBIDDEN,
    CODE_NOT_FOUND,
    CODE_PARAM_ERROR,
    MSG_FORBIDDEN,
)
from app.core.data_scope import build_data_scope, is_in_scope
from common.db.repository import Repository
from common.models.reply_models import DefaultReply, GoodsReply
from common.models.shop_models import Shop
from common.models.user_models import SysUser
from common.schemas.common import ApiResponse, error_response, success_response
from common.utils.time_utils import safe_isoformat

# 允许的回复类型（枚举键，与 sys_dict 的 reply_type 字典一致，需求 24.7）。
ALLOWED_REPLY_TYPES: Tuple[str, ...] = ("text", "image")


# ----------------------------------------------------------------------
# 序列化辅助
# ----------------------------------------------------------------------
def serialize_default_reply(reply: DefaultReply) -> Dict[str, Any]:
    """将默认回复模型序列化为对外字典。

    Args:
        reply: 默认回复模型实例。

    Returns:
        默认回复信息字典（含店铺主键、内容、启停用与审计时间）。
    """
    return {
        "id": reply.id,
        "shop_pk": reply.shop_pk,
        "content": reply.content,
        "enabled": bool(reply.enabled),
        "reply_once": bool(getattr(reply, "reply_once", False)),
        "created_at": safe_isoformat(reply.created_at),
        "updated_at": safe_isoformat(reply.updated_at),
    }


def serialize_goods_reply(reply: GoodsReply) -> Dict[str, Any]:
    """将商品专属回复模型序列化为对外字典。

    Args:
        reply: 商品专属回复模型实例。

    Returns:
        商品专属回复信息字典（含 goods_id、回复类型 / 内容、启停用与审计时间）。
    """
    return {
        "id": reply.id,
        "shop_pk": reply.shop_pk,
        "goods_id": reply.goods_id,
        "reply_type": reply.reply_type,
        "reply_content": reply.reply_content,
        "enabled": bool(reply.enabled),
        "created_at": safe_isoformat(reply.created_at),
        "updated_at": safe_isoformat(reply.updated_at),
    }


# ----------------------------------------------------------------------
# 数据范围与校验辅助
# ----------------------------------------------------------------------
def _resolve_shop_in_scope(
    session: Session, user: SysUser, shop_pk: int
) -> Tuple[Optional[Shop], Optional[ApiResponse]]:
    """校验店铺存在且在当前用户数据范围内，返回 (店铺, 失败响应)。

    依据需求 3.7 / Property 8：管理员不受限；非管理员仅可操作本人创建或被授权
    的店铺。店铺不存在返回 NOT_FOUND；越权返回「无访问权限」。

    Args:
        session: 数据库会话。
        user: 当前登录用户。
        shop_pk: 店铺主键 shop.id。

    Returns:
        二元组 (店铺实例, 失败响应)：校验通过时第二项为 None；失败时第一项为 None。
    """
    shop = Repository(Shop, session).get(shop_pk)
    if shop is None:
        return None, error_response(CODE_NOT_FOUND, "店铺不存在")
    scope = build_data_scope(user, session=session)
    if not is_in_scope(scope, shop.owner_user_id):
        return None, error_response(CODE_FORBIDDEN, MSG_FORBIDDEN)
    return shop, None


def _normalize_reply_type(reply_type: Optional[str]) -> str:
    """规整回复类型：为空时默认 text；非法值回退 text（仅接受字典内枚举）。

    Args:
        reply_type: 回复类型键（text/image）。

    Returns:
        合法的回复类型键。
    """
    if reply_type and reply_type in ALLOWED_REPLY_TYPES:
        return reply_type
    return "text"


# ----------------------------------------------------------------------
# 默认回复配置（需求 7.1）
# ----------------------------------------------------------------------
def get_default_reply(
    session: Session, user: SysUser, shop_pk: int
) -> ApiResponse:
    """查询某店铺的默认回复配置（需求 7.1 配套）。

    Args:
        session: 数据库会话。
        user: 当前登录用户。
        shop_pk: 店铺主键 shop.id。

    Returns:
        统一响应体：存在则返回配置；不存在返回 data=None（未配置）。
    """
    _, denied = _resolve_shop_in_scope(session, user, shop_pk)
    if denied is not None:
        return denied
    reply = Repository(DefaultReply, session).get_by(shop_pk=shop_pk)
    data = serialize_default_reply(reply) if reply is not None else None
    return success_response(data=data, message="查询成功")


def save_default_reply(
    session: Session,
    user: SysUser,
    shop_pk: int,
    content: str,
    enabled: bool = True,
    reply_once: bool = False,
) -> ApiResponse:
    """保存（upsert）某店铺的默认回复配置（需求 7.1）。

    同 ``shop_pk`` 幂等更新而非新建（每店铺仅一条默认回复配置）。内容不可为空。

    Args:
        session: 数据库会话。
        user: 当前登录用户。
        shop_pk: 店铺主键 shop.id。
        content: 默认回复内容。
        enabled: 是否启用，默认 True。
        reply_once: 是否只回复一次（同一客户仅发送一次默认回复），默认 False。

    Returns:
        统一响应体：成功返回保存后的默认回复配置。
    """
    _, denied = _resolve_shop_in_scope(session, user, shop_pk)
    if denied is not None:
        return denied
    if not content or not content.strip():
        return error_response(CODE_PARAM_ERROR, "默认回复内容不能为空")

    reply = Repository(DefaultReply, session).upsert(
        biz_keys={"shop_pk": shop_pk},
        values={
            "content": content.strip(),
            "enabled": bool(enabled),
            "reply_once": bool(reply_once),
            "created_by": user.id,
        },
    )
    return success_response(
        data=serialize_default_reply(reply), message="保存成功"
    )


def set_default_reply_status(
    session: Session, user: SysUser, shop_pk: int, enabled: bool
) -> ApiResponse:
    """启用 / 停用某店铺的默认回复配置。

    Args:
        session: 数据库会话。
        user: 当前登录用户。
        shop_pk: 店铺主键 shop.id。
        enabled: True=启用，False=停用。

    Returns:
        统一响应体：成功返回更新后的默认回复配置；未配置返回失败。
    """
    _, denied = _resolve_shop_in_scope(session, user, shop_pk)
    if denied is not None:
        return denied
    repo = Repository(DefaultReply, session)
    reply = repo.get_by(shop_pk=shop_pk)
    if reply is None:
        return error_response(CODE_NOT_FOUND, "默认回复未配置")
    repo.update(reply.id, enabled=bool(enabled))
    message = "已启用" if enabled else "已停用"
    return success_response(
        data=serialize_default_reply(reply), message=message
    )


# ----------------------------------------------------------------------
# 商品专属回复 CRUD（绑定 goods_id，优先级高于默认，需求 7.2/7.3/7.4/7.5）
# ----------------------------------------------------------------------
def create_goods_reply(
    session: Session,
    user: SysUser,
    shop_pk: int,
    goods_id: str,
    reply_content: str,
    reply_type: Optional[str] = "text",
    enabled: bool = True,
) -> ApiResponse:
    """新增 / upsert 商品专属回复（绑定 goods_id，需求 7.3）。

    同 (shop_pk, goods_id) 幂等更新而非新建（一个商品在同店铺下仅一条专属回复
    配置，需求 7.3 配套）。goods_id 与回复内容不可为空。

    Args:
        session: 数据库会话。
        user: 当前登录用户。
        shop_pk: 店铺主键 shop.id。
        goods_id: 拼多多商品业务标识。
        reply_content: 回复内容。
        reply_type: 回复类型（text/image），默认 text。
        enabled: 是否启用，默认 True。

    Returns:
        统一响应体：成功返回保存后的商品专属回复。
    """
    _, denied = _resolve_shop_in_scope(session, user, shop_pk)
    if denied is not None:
        return denied
    if not goods_id or not goods_id.strip():
        return error_response(CODE_PARAM_ERROR, "商品 goods_id 不能为空")
    if not reply_content or not reply_content.strip():
        return error_response(CODE_PARAM_ERROR, "回复内容不能为空")

    reply = Repository(GoodsReply, session).upsert(
        biz_keys={"shop_pk": shop_pk, "goods_id": goods_id.strip()},
        values={
            "reply_type": _normalize_reply_type(reply_type),
            "reply_content": reply_content.strip(),
            "enabled": bool(enabled),
            "created_by": user.id,
        },
    )
    return success_response(
        data=serialize_goods_reply(reply), message="保存成功"
    )


def update_goods_reply(
    session: Session,
    user: SysUser,
    reply_id: int,
    reply_content: Optional[str] = None,
    reply_type: Optional[str] = None,
    enabled: Optional[bool] = None,
) -> ApiResponse:
    """按主键更新商品专属回复（需求 7.3 配套）。

    仅更新传入的非空字段；店铺归属经数据范围隔离校验。

    Args:
        session: 数据库会话。
        user: 当前登录用户。
        reply_id: 商品专属回复主键。
        reply_content: 新回复内容；None 表示不修改。
        reply_type: 新回复类型；None 表示不修改。
        enabled: 新启停用状态；None 表示不修改。

    Returns:
        统一响应体：成功返回更新后的商品专属回复。
    """
    repo = Repository(GoodsReply, session)
    reply = repo.get(reply_id)
    if reply is None:
        return error_response(CODE_NOT_FOUND, "商品专属回复不存在")
    _, denied = _resolve_shop_in_scope(session, user, reply.shop_pk)
    if denied is not None:
        return denied

    values: Dict[str, Any] = {}
    if reply_content is not None:
        if not reply_content.strip():
            return error_response(CODE_PARAM_ERROR, "回复内容不能为空")
        values["reply_content"] = reply_content.strip()
    if reply_type is not None:
        values["reply_type"] = _normalize_reply_type(reply_type)
    if enabled is not None:
        values["enabled"] = bool(enabled)

    if values:
        repo.update(reply_id, **values)
    return success_response(
        data=serialize_goods_reply(reply), message="更新成功"
    )


def set_goods_reply_status(
    session: Session, user: SysUser, reply_id: int, enabled: bool
) -> ApiResponse:
    """启用 / 停用商品专属回复。

    Args:
        session: 数据库会话。
        user: 当前登录用户。
        reply_id: 商品专属回复主键。
        enabled: True=启用，False=停用。

    Returns:
        统一响应体：成功返回更新后的商品专属回复。
    """
    repo = Repository(GoodsReply, session)
    reply = repo.get(reply_id)
    if reply is None:
        return error_response(CODE_NOT_FOUND, "商品专属回复不存在")
    _, denied = _resolve_shop_in_scope(session, user, reply.shop_pk)
    if denied is not None:
        return denied
    repo.update(reply_id, enabled=bool(enabled))
    message = "已启用" if enabled else "已停用"
    return success_response(
        data=serialize_goods_reply(reply), message=message
    )


def delete_goods_reply(
    session: Session, user: SysUser, reply_id: int
) -> ApiResponse:
    """逻辑删除商品专属回复（禁止物理删除，需求 24.6）。

    通过状态字段 enabled=False 实现逻辑删除，记录保留。

    Args:
        session: 数据库会话。
        user: 当前登录用户。
        reply_id: 商品专属回复主键。

    Returns:
        统一响应体：成功返回 data=None。
    """
    repo = Repository(GoodsReply, session)
    reply = repo.get(reply_id)
    if reply is None:
        return error_response(CODE_NOT_FOUND, "商品专属回复不存在")
    _, denied = _resolve_shop_in_scope(session, user, reply.shop_pk)
    if denied is not None:
        return denied
    repo.soft_delete(reply_id, field="enabled")
    return success_response(data=None, message="已删除")


def list_goods_replies(
    session: Session,
    user: SysUser,
    shop_pk: int,
    page: Any = 1,
    page_size: Any = 20,
    enabled: Optional[bool] = None,
) -> ApiResponse:
    """后端分页查询某店铺的商品专属回复列表（需求 7.5）。

    默认按创建时间倒序（仓储层自动探测时间字段）。可按启停用状态筛选。

    Args:
        session: 数据库会话。
        user: 当前登录用户。
        shop_pk: 店铺主键 shop.id。
        page: 页码（从 1 开始，将被规整）。
        page_size: 每页条数（10/20/50/100，将被规整）。
        enabled: 按启停用筛选；None 表示不筛选。

    Returns:
        统一响应体：data 为分页结构 {list, total, page, page_size}。
    """
    _, denied = _resolve_shop_in_scope(session, user, shop_pk)
    if denied is not None:
        return denied

    filters: Dict[str, Any] = {"shop_pk": shop_pk}
    if enabled is not None:
        filters["enabled"] = bool(enabled)

    page_result = Repository(GoodsReply, session).paginate(
        page=page, page_size=page_size, filters=filters
    )
    serialized: List[Dict[str, Any]] = [
        serialize_goods_reply(reply) for reply in page_result.items
    ]
    data = {
        "list": serialized,
        "total": page_result.total,
        "page": page_result.page,
        "page_size": page_result.page_size,
    }
    return success_response(data=data, message="查询成功")


__all__ = [
    "ALLOWED_REPLY_TYPES",
    "serialize_default_reply",
    "serialize_goods_reply",
    "get_default_reply",
    "save_default_reply",
    "set_default_reply_status",
    "create_goods_reply",
    "update_goods_reply",
    "set_goods_reply_status",
    "delete_goods_reply",
    "list_goods_replies",
]
