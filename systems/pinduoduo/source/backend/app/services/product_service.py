# -*- coding: utf-8 -*-
"""
backend.app.services.product_service —— 商品管理业务服务
========================================================
本文件用途：实现 backend 服务的「商品管理」业务逻辑，供 products 路由复用，满足
需求 15（商品管理）：

- ``list_products(...)``：商品列表后端分页（需求 15.1）。按北京时间倒序、后端分页
  （默认 20，可选 10/20/50/100），并做数据范围隔离（非管理员仅见本人 / 被授权
  店铺的商品）。
- ``sync_products(...)``：触发商品同步（需求 15.2 / 15.3 / 15.4）。经 HTTP 调用
  websocket 服务从拼多多店铺拉取商品，按 (shop_pk, goods_id) upsert 入库；当因
  Cookie 缺少有效 anti-content 签名导致拉取失败时，**终止本次同步**、返回固定
  中文提示并记录系统日志（需求 15.3 / 26.2），不影响其它流程。
- ``create_goods_reply_from_product(...)``：从商品记录创建商品专属回复（需求 15.5），
  复用 reply_service 的 upsert 逻辑，关联对应 goods_id 并持久化。
- ``create_product_knowledge_from_product(...)``：从商品记录创建 / 更新商品知识
  （需求 15.5），按 (shop_pk, goods_id) upsert，关联对应 goods_id 并持久化。

实现约束（开发规范）：
- 统一响应体由 common.schemas.common 构造，HTTP 恒 200（规范 1-3）。
- 所有数据访问经 common.db.repository 参数化查询，禁止拼接 SQL（规范 16）。
- 数据范围隔离统一经 app.core.data_scope（规范 42 集中判权 / 需求 3.7）。
- 同步入口经环境变量配置的服务地址 HTTP 调用 websocket，禁止写死 localhost
  （规范 21）；拉取与 upsert 解耦，签名缺失走统一降级提示（需求 26）。
- upsert 幂等：同 (shop_pk, goods_id) 更新而非新建（需求 15.4）。
- 时间统一北京时间（规范 17 / 需求 24.8）；导入置顶（规范 51）；中文注释（规范 37）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.core.business_codes import (
    CODE_EXTERNAL_ERROR,
    CODE_FORBIDDEN,
    CODE_NOT_FOUND,
    CODE_SIGNATURE_MISSING,
    MSG_FORBIDDEN,
    MSG_SIGNATURE_MISSING,
)
from app.core.data_scope import build_data_scope, is_in_scope
from app.services import (
    product_spec_backfill,
    product_spec_codec,
    product_sync_client,
    reply_service,
)
from common.db.repository import Repository
from common.models.knowledge_models import Product, ProductKnowledge
from common.models.log_models import SystemLog
from common.models.shop_models import Shop
from common.models.user_models import SysUser
from common.schemas.common import ApiResponse, error_response, success_response
from common.utils.time_utils import now_beijing_naive, safe_isoformat

# 商品启停用状态值（与 Product.status 约定一致：1=启用，0=停用）。
PRODUCT_STATUS_ENABLED: int = 1

# 系统日志来源模块标识（需求 15.3 记系统日志）。
_LOG_MODULE: str = "product_sync"


# ----------------------------------------------------------------------
# 序列化
# ----------------------------------------------------------------------
def serialize_product(product: Product) -> Dict[str, Any]:
    """将商品模型序列化为对外字典（需求 15.1）。

    输出名称、goods_id、价格、销量、缩略图、规格（解码为列表）与北京时间审计字段。

    Args:
        product: 商品模型实例。

    Returns:
        商品信息字典。
    """
    return {
        "id": product.id,
        "shop_pk": product.shop_pk,
        "goods_id": product.goods_id,
        "goods_name": product.goods_name,
        "price": float(product.price) if product.price is not None else None,
        "sold_quantity": product.sold_quantity,
        "thumb_url": product.thumb_url,
        "specifications": product_spec_codec.decode_specifications(
            product.specifications
        ),
        "status": product.status,
        "platform_on_sale": product.platform_on_sale,
        "created_at": safe_isoformat(product.created_at),
        "updated_at": safe_isoformat(product.updated_at),
        "synced_at": safe_isoformat(product.synced_at),
        "specs_updated_at": safe_isoformat(product.specs_updated_at),
        "specs_checked_at": safe_isoformat(product.specs_checked_at),
        "specs_error": product.specs_error,
    }


# ----------------------------------------------------------------------
# 数据范围与校验辅助
# ----------------------------------------------------------------------
def _resolve_shop_in_scope(
    session: Session, user: SysUser, shop_pk: int
) -> Tuple[Optional[Shop], Optional[ApiResponse]]:
    """校验店铺存在且在当前用户数据范围内，返回 (店铺, 失败响应)。

    依据需求 3.7：管理员不受限；非管理员仅可操作本人创建或被授权的店铺。店铺
    不存在返回 NOT_FOUND；越权返回「无访问权限」。

    Args:
        session: 数据库会话。
        user: 当前登录用户。
        shop_pk: 店铺主键 shop.id。

    Returns:
        二元组 (店铺实例, 失败响应)：校验通过时第二项为 None；失败时第一项为 None。
    """
    shop = Repository(Shop, session).get(shop_pk)
    if shop is None or shop.merged_into_shop_pk is not None:
        return None, error_response(CODE_NOT_FOUND, "店铺不存在")
    scope = build_data_scope(user, session=session)
    if not is_in_scope(scope, shop.owner_user_id):
        return None, error_response(CODE_FORBIDDEN, MSG_FORBIDDEN)
    return shop, None


def _write_system_log(session: Session, level: str, content: str) -> None:
    """写入一条系统日志（北京时间，禁止 debug 级别，规范 38）。

    Args:
        session: 数据库会话。
        level: 日志级别（info/warning/error）。
        content: 日志内容（中文）。
    """
    Repository(SystemLog, session).create(
        level=level,
        module=_LOG_MODULE,
        content=content,
        log_time=now_beijing_naive(),
    )


# ----------------------------------------------------------------------
# 商品列表（后端分页 + 数据范围隔离 —— 需求 15.1 / 3.7）
# ----------------------------------------------------------------------
def list_products(
    session: Session,
    user: SysUser,
    shop_pk: int,
    page: Any = 1,
    page_size: Any = 20,
    status: Optional[int] = None,
) -> ApiResponse:
    """分页查询某店铺的商品列表（需求 15.1）。

    按北京时间（created_at）倒序、后端分页（默认 20，可选 10/20/50/100）返回商品
    记录（名称、goods_id、价格、销量、缩略图）。受数据范围隔离约束（需求 3.7）。

    Args:
        session: 数据库会话。
        user: 当前登录用户。
        shop_pk: 店铺主键 shop.id。
        page: 页码（从 1 开始，将被规整）。
        page_size: 每页条数（10/20/50/100，将被规整）。
        status: 按状态筛选（1=启用，0=停用）；None 表示不筛选。

    Returns:
        统一响应体：data 为分页结构 {list, total, page, page_size}。
    """
    _, denied = _resolve_shop_in_scope(session, user, shop_pk)
    if denied is not None:
        return denied

    filters: Dict[str, Any] = {"shop_pk": shop_pk}
    if status is not None:
        filters["status"] = status

    page_result = Repository(Product, session).paginate(
        page=page, page_size=page_size, filters=filters
    )
    serialized: List[Dict[str, Any]] = [
        serialize_product(product) for product in page_result.items
    ]
    data = {
        "list": serialized,
        "total": page_result.total,
        "page": page_result.page,
        "page_size": page_result.page_size,
    }
    return success_response(data=data, message="查询成功")


# ----------------------------------------------------------------------
# 商品详情（库内基础信息 + 实时拉取规格 —— 需求 15）
# ----------------------------------------------------------------------
def get_product_detail(
    session: Session, user: SysUser, product_id: int, refresh_specs: bool = False
) -> ApiResponse:
    """查看单个商品详情（需求 15）。

    流程：
    1. 据商品主键定位商品记录，不存在返回 NOT_FOUND；
    2. 数据范围隔离校验店铺归属（需求 3.7）；
    3. 默认只读取库内信息，不因规格为空而阻塞页面；
    4. refresh_specs=True 时才经 HTTP 查询实时规格并落库；
    5. 实时查询失败（含签名缺失 / 外部依赖不可用）：**降级**仅返回库内基础信息，
       保留已存规格并附中文提示，不整体失败。

    Args:
        session: 数据库会话。
        user: 当前登录用户。
        product_id: 商品记录主键 product.id。

    Returns:
        统一响应体：data 为商品详情结构（基础字段 + specifications + detail_message）。
    """
    product = Repository(Product, session).get(product_id)
    if product is None:
        return error_response(CODE_NOT_FOUND, "商品不存在")

    shop, denied = _resolve_shop_in_scope(session, user, product.shop_pk)
    if denied is not None:
        return denied

    # 库内基础信息（始终返回，作为详情主体；specifications 已解码为列表）。
    detail = serialize_product(product)
    detail["detail_message"] = ""

    # 默认始终读库，空规格也不能触发外部请求阻塞用户打开详情。
    if not refresh_specs:
        if product.specs_error:
            detail["detail_message"] = product.specs_error
        elif not detail.get("specifications"):
            detail["detail_message"] = "尚未获取商品规格，可点击“更新规格”补充。"
        return success_response(data=detail, message="查询成功")

    # 用户显式刷新：经 HTTP 查询规格；失败不覆盖已保存内容。
    result = product_sync_client.fetch_product_detail(
        shop_id=shop.shop_id,
        owner_user_id=shop.owner_user_id,
        goods_id=product.goods_id,
    )
    product.specs_checked_at = now_beijing_naive()

    if result.ok:
        live = result.detail or {}
        specifications = live.get("specifications")
        specifications = specifications if isinstance(specifications, list) else []
        # 实时规格落库（仅在拿到非空规格时更新，避免覆盖历史规格，需求 15）。
        encoded = product_spec_codec.encode_specifications(specifications)
        if encoded is not None:
            Repository(Product, session).update(
                product_id, specifications=encoded
            )
            detail["specifications"] = [
                str(item) for item in specifications if item is not None
            ]
            product.specs_updated_at = now_beijing_naive()
            product.specs_error = None
        else:
            product.specs_error = "平台本次未返回规格，已保留原数据"
        # 实时无规格时保留库内已存规格（detail["specifications"] 已由序列化给出）。
    elif result.signature_missing:
        # 签名缺失：降级返回库内信息并提示重新登录（需求 26.2），不整体失败。
        detail["detail_message"] = MSG_SIGNATURE_MISSING
    else:
        # 其它外部依赖失败：降级返回库内信息并附中文提示。
        detail["detail_message"] = result.message or "实时规格信息暂不可用"

    if not result.ok:
        product.specs_error = detail["detail_message"]
    detail.update(specs_checked_at=safe_isoformat(product.specs_checked_at),
                  specs_updated_at=safe_isoformat(product.specs_updated_at),
                  specs_error=product.specs_error)

    return success_response(data=detail, message="查询成功")


# ----------------------------------------------------------------------
# 触发商品同步（HTTP 调用 websocket 拉取 + upsert + 签名缺失降级 —— 需求 15.2/15.3/15.4）
# ----------------------------------------------------------------------
def sync_products(session: Session, user: SysUser, shop_pk: int) -> ApiResponse:
    """兼容旧入口：与新界面共用持久同步队列，不再直接拉取平台。"""
    from app.services import product_jobs
    shop, denied = _resolve_shop_in_scope(session, user, shop_pk)
    if denied is not None:
        return denied
    if shop.status != 1:
        return error_response(CODE_PARAM_ERROR, '店铺已停用，请先启用店铺')
    state = product_jobs.enqueue(session, shop, actor_id=user.id)
    session.commit()
    product_jobs.wake()
    return success_response(data=state, message='已加入后台同步')


def _upsert_pulled_products(
    session: Session, shop_pk: int, products: List[Dict[str, Any]]
) -> int:
    """将拉取到的商品按 (shop_pk, goods_id) upsert 入库（需求 15.4）。

    缺少 goods_id 的脏数据条目跳过；同 (shop_pk, goods_id) 更新而非新建（幂等）。

    Args:
        session: 数据库会话。
        shop_pk: 店铺主键 shop.id。
        products: 拉取到的商品原始字典列表。

    Returns:
        实际 upsert 入库的商品条数。
    """
    # 仅由已校验完整的在售快照调用；不能用部分分页推断下架。
    from sqlalchemy import select
    now = now_beijing_naive()
    current = list(session.scalars(select(Product).where(Product.shop_pk == shop_pk).with_for_update()))
    by_id = {row.goods_id: row for row in current}
    seen = {str(item['goods_id']).strip() for item in products}
    for row in current:
        row.platform_on_sale = 1 if row.goods_id in seen else 0
        row.synced_at = now
    repo = Repository(Product, session)
    synced = 0
    for item in products:
        if not isinstance(item, dict):
            continue
        goods_id = item.get("goods_id")
        if goods_id is None or not str(goods_id).strip():
            # 缺少业务键的脏数据跳过，避免污染商品表。
            continue
        repo.upsert(
            biz_keys={"shop_pk": shop_pk, "goods_id": str(goods_id).strip()},
            values={
                "goods_name": item.get("goods_name"),
                "price": item.get("price"),
                "sold_quantity": item.get("sold_quantity"),
                "thumb_url": item.get("thumb_url"),
                "status": by_id[str(goods_id).strip()].status if str(goods_id).strip() in by_id else PRODUCT_STATUS_ENABLED,
                "platform_on_sale": 1,
                "synced_at": now,
            },
        )
        synced += 1
    session.flush()
    return synced


# ----------------------------------------------------------------------
# 从商品记录创建商品专属回复 / 商品知识（需求 15.5）
# ----------------------------------------------------------------------
def create_goods_reply_from_product(
    session: Session,
    user: SysUser,
    product_id: int,
    reply_content: str,
    reply_type: Optional[str] = "text",
    enabled: bool = True,
) -> ApiResponse:
    """从商品记录创建商品专属回复（需求 15.5）。

    据商品主键定位其 (shop_pk, goods_id)，复用 reply_service.create_goods_reply
    的 upsert 逻辑关联对应 goods_id 并持久化（同店铺同商品幂等）。

    Args:
        session: 数据库会话。
        user: 当前登录用户。
        product_id: 商品记录主键 product.id。
        reply_content: 回复内容。
        reply_type: 回复类型（text/image），默认 text。
        enabled: 是否启用，默认 True。

    Returns:
        统一响应体：成功返回保存后的商品专属回复。
    """
    product = Repository(Product, session).get(product_id)
    if product is None:
        return error_response(CODE_NOT_FOUND, "商品不存在")
    # 数据范围隔离 + goods_id 关联（reply_service 内部再次校验店铺归属）。
    return reply_service.create_goods_reply(
        session,
        user,
        shop_pk=product.shop_pk,
        goods_id=product.goods_id,
        reply_content=reply_content,
        reply_type=reply_type,
        enabled=enabled,
    )


def create_product_knowledge_from_product(
    session: Session,
    user: SysUser,
    product_id: int,
    extracted_content: Optional[str] = None,
    specifications: Optional[str] = None,
) -> ApiResponse:
    """从商品记录创建 / 更新商品知识（需求 15.5）。

    据商品主键定位其 (shop_pk, goods_id)，按 (shop_pk, goods_id) upsert 商品知识，
    沿用商品的名称 / 价格 / 销量 / 缩略图，并记录最近提取时间（北京时间，需求 9.1）。

    Args:
        session: 数据库会话。
        user: 当前登录用户。
        product_id: 商品记录主键 product.id。
        extracted_content: 供 AI 检索的抽取内容；None 表示不设置。
        specifications: 商品规格（JSON 文本）；None 表示不设置。

    Returns:
        统一响应体：成功返回保存后的商品知识。
    """
    product = Repository(Product, session).get(product_id)
    if product is None:
        return error_response(CODE_NOT_FOUND, "商品不存在")

    # 数据范围隔离：非管理员仅可操作本人 / 被授权店铺下的商品（需求 3.7）。
    _, denied = _resolve_shop_in_scope(session, user, product.shop_pk)
    if denied is not None:
        return denied

    knowledge = Repository(ProductKnowledge, session).upsert(
        biz_keys={"shop_pk": product.shop_pk, "goods_id": product.goods_id},
        values={
            "goods_name": product.goods_name,
            "price": product.price,
            "sold_quantity": product.sold_quantity,
            "thumb_url": product.thumb_url,
            "specifications": specifications,
            "extracted_content": extracted_content,
            "last_extracted_at": now_beijing_naive(),
            "status": PRODUCT_STATUS_ENABLED,
            "created_by": user.id,
        },
    )
    data = {
        "id": knowledge.id,
        "shop_pk": knowledge.shop_pk,
        "goods_id": knowledge.goods_id,
        "goods_name": knowledge.goods_name,
        "last_extracted_at": safe_isoformat(knowledge.last_extracted_at),
    }
    return success_response(data=data, message="保存成功")


__all__ = [
    "PRODUCT_STATUS_ENABLED",
    "serialize_product",
    "list_products",
    "get_product_detail",
    "sync_products",
    "create_goods_reply_from_product",
    "create_product_knowledge_from_product",
]
