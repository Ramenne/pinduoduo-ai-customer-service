# -*- coding: utf-8 -*-
"""
backend.app.services.product_sync_client —— 商品拉取（经统一服务间客户端调用 websocket）
======================================================================================
本文件用途：商品同步（需求 15.2）时，由 **websocket 服务**复用拼多多 WebSocket /
HTTP 能力从拼多多店铺拉取商品列表（依赖 anti-content 签名）。按设计「多服务拆分
架构」，backend 不直接对接拼多多，而是通过 **HTTP 调用** websocket 服务的商品拉取
接口完成拉取，地址经环境变量 ``WEBSOCKET_SERVICE_URL`` 配置，**禁止写死 localhost**
（规范 21）。

拉取结果统一规整为 ``ProductPullResult``：
- ``ok``：拉取是否成功（拿到商品列表）。
- ``signature_missing``：是否因 Cookie 缺少有效 anti-content 签名而失败（需求
  15.3 / 26.2）——此情形需由调用方终止同步、返回固定中文提示并记系统日志。
- ``products``：拉取到的商品原始字典列表（成功时）。
- ``message``：失败原因（中文）。

实现要点（任务 19.1 统一）：
- 复用 common 统一服务间 HTTP 客户端 ``common.services.service_client``（规范 36/52），
  不再各自维护 urllib 客户端。
- 网络不可达 / 超时 / 非 2xx / 解析失败一律视为「外部依赖暂不可用」，返回 ok=False
  并附中文原因，不抛异常打断 backend 主流程（健壮性兜底，需求 26）。
- websocket 服务以统一响应体 / 约定字段表达「签名缺失」，本模块据此置位
  ``signature_missing``，由调用方统一处理。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from common.services import service_client

logger = logging.getLogger(__name__)

# 商品拉取请求超时（秒）：拉取可能涉及外部接口，给予相对宽裕但有界的超时。
_PULL_TIMEOUT_SECONDS: float = 30.0

# websocket 服务「商品拉取」接口的相对路径（与 websocket 服务路由约定一致）。
_PULL_PATH: str = "/api/v1/products/pull"

# websocket 服务「商品详情」接口的相对路径（与 websocket 服务路由约定一致）。
_DETAIL_PATH: str = "/api/v1/products/detail"


@dataclass
class ProductPullResult:
    """商品拉取结果（对 websocket 服务响应的统一规整）。

    Attributes:
        ok: 是否成功拉取到商品列表。
        signature_missing: 是否因缺少有效 anti-content 签名而失败（需求 15.3）。
        products: 拉取到的商品原始字典列表（ok=True 时有效）。
        message: 失败原因（中文）；成功时为空字符串。
    """

    ok: bool = False
    signature_missing: bool = False
    products: List[Dict[str, Any]] = field(default_factory=list)
    message: str = ""


@dataclass
class ProductDetailResult:
    """商品详情查询结果（对 websocket 服务响应的统一规整）。

    Attributes:
        ok: 是否成功查询到商品详情。
        signature_missing: 是否因缺少有效 anti-content 签名而失败（需求 26.2）。
        detail: 查询到的商品详情字典（ok=True 时有效），含 goods_id / goods_name /
            specifications。
        message: 失败原因（中文）；成功时为空字符串。
    """

    ok: bool = False
    signature_missing: bool = False
    detail: Dict[str, Any] = field(default_factory=dict)
    message: str = ""


def pull_products(
    shop_pk: int, shop_id: str, owner_user_id: Optional[int]
) -> ProductPullResult:
    """经统一服务间客户端调用 websocket 服务拉取指定店铺商品（需求 15.2 / 15.3）。

    地址由环境变量配置（禁止写死 localhost）。无论 websocket 服务是否可达，本函数
    都不抛异常：成功返回含 ``products`` 的结果；因签名缺失失败时置位
    ``signature_missing``；其它失败返回 ok=False 并附中文原因。

    Args:
        shop_pk: 店铺主键（shop.id）。
        shop_id: 拼多多店铺业务标识。
        owner_user_id: 店铺归属用户 ID（用于 websocket 侧定位凭据）。

    Returns:
        规整后的 ``ProductPullResult``。
    """
    response = service_client.post_json(
        service_client.websocket_base_url(),
        _PULL_PATH,
        {"shop_pk": shop_pk, "shop_id": shop_id, "owner_user_id": owner_user_id},
        timeout=_PULL_TIMEOUT_SECONDS,
    )

    # 传输层失败（网络不可达 / 超时 / 非 2xx / 解析失败）：视为外部依赖暂不可用。
    if not response.ok or response.body is None:
        logger.warning(
            "调用 websocket 拉取商品失败：shop_id=%s err=%s",
            shop_id,
            response.message or response.error,
        )
        return ProductPullResult(ok=False, message="商品拉取服务暂不可用，请稍后重试")

    return _parse_pull_body(response.body, shop_id)


def _parse_pull_body(body: Dict[str, Any], shop_id: str) -> ProductPullResult:
    """将 websocket 服务的拉取响应体规整为 ``ProductPullResult``。

    约定 websocket 服务以统一响应体返回：
    - 成功：``{"success": true, "data": {"products": [...]}}``；
    - 签名缺失：``{"success": false, "signature_missing": true, "message": "..."}``；
    - 其它失败：``{"success": false, "message": "..."}``。

    Args:
        body: 已解析的响应体字典。
        shop_id: 拼多多店铺业务标识（用于日志）。

    Returns:
        规整后的 ``ProductPullResult``。
    """
    if body.get("success"):
        data = body.get("data") or {}
        products = data.get("products") if isinstance(data, dict) else None
        if (data.get('complete') is not True or not isinstance(products, list)
                or any(not isinstance(p, dict) or not p.get('goods_id') for p in products)
                or len({str(p['goods_id']) for p in products}) != len(products)):
            return ProductPullResult(ok=False, message='商品响应不完整，未更新本地目录')
        return ProductPullResult(ok=True, products=products)

    # 失败：判定是否为签名缺失（需求 15.3）。signature_missing 兼容置于顶层或 data 内。
    data = body.get("data") if isinstance(body.get("data"), dict) else {}
    signature_missing = bool(
        body.get("signature_missing") or (data or {}).get("signature_missing")
    )
    message = body.get("message") or "商品拉取失败"
    if signature_missing:
        logger.warning("店铺 shop_id=%s 因签名缺失拉取商品失败", shop_id)
    return ProductPullResult(
        ok=False, signature_missing=signature_missing, message=str(message)
    )


def fetch_product_detail(
    shop_id: str, owner_user_id: Optional[int], goods_id: str
) -> ProductDetailResult:
    """经统一服务间客户端调用 websocket 服务查询指定商品的实时详情（需求 15）。

    地址由环境变量配置（禁止写死 localhost）。无论 websocket 服务是否可达，本函数
    都不抛异常：成功返回含 ``detail`` 的结果；因签名缺失失败时置位
    ``signature_missing``；其它失败返回 ok=False 并附中文原因。

    Args:
        shop_id: 拼多多店铺业务标识。
        owner_user_id: 店铺归属用户 ID（用于 websocket 侧定位凭据）。
        goods_id: 拼多多商品业务标识。

    Returns:
        规整后的 ``ProductDetailResult``。
    """
    response = service_client.post_json(
        service_client.websocket_base_url(),
        _DETAIL_PATH,
        {"shop_id": shop_id, "owner_user_id": owner_user_id, "goods_id": goods_id},
        timeout=10.0,
    )

    # 传输层失败（网络不可达 / 超时 / 非 2xx / 解析失败）：视为外部依赖暂不可用。
    if not response.ok or response.body is None:
        logger.warning(
            "调用 websocket 查询商品详情失败：shop_id=%s goods_id=%s err=%s",
            shop_id,
            goods_id,
            response.message or response.error,
        )
        return ProductDetailResult(
            ok=False, message="实时规格服务暂不可用，已保留商品已有信息；可稍后重试。"
        )

    return _parse_detail_body(response.body, shop_id, goods_id)


def _parse_detail_body(
    body: Dict[str, Any], shop_id: str, goods_id: str
) -> ProductDetailResult:
    """将 websocket 服务的商品详情响应体规整为 ``ProductDetailResult``。

    约定 websocket 服务以统一响应体返回：
    - 成功：``{"success": true, "data": {"detail": {...}}}``；
    - 签名缺失：``{"success": false, "signature_missing": true, "message": "..."}``；
    - 其它失败：``{"success": false, "message": "..."}``。

    Args:
        body: 已解析的响应体字典。
        shop_id: 拼多多店铺业务标识（用于日志）。
        goods_id: 拼多多商品业务标识（用于日志）。

    Returns:
        规整后的 ``ProductDetailResult``。
    """
    if body.get("success"):
        data = body.get("data") or {}
        detail = data.get("detail") if isinstance(data, dict) else None
        if not isinstance(detail, dict):
            detail = {}
        return ProductDetailResult(ok=True, detail=detail)

    # 失败：判定是否为签名缺失（signature_missing 兼容置于顶层或 data 内）。
    data = body.get("data") if isinstance(body.get("data"), dict) else {}
    signature_missing = bool(
        body.get("signature_missing") or (data or {}).get("signature_missing")
    )
    message = body.get("message") or "商品详情查询失败"
    if signature_missing:
        logger.warning(
            "店铺 shop_id=%s 商品 goods_id=%s 因签名缺失查询详情失败",
            shop_id,
            goods_id,
        )
    return ProductDetailResult(
        ok=False, signature_missing=signature_missing, message=str(message)
    )


__all__ = [
    "ProductPullResult",
    "ProductDetailResult",
    "pull_products",
    "fetch_product_detail",
]
