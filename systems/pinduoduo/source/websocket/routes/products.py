# -*- coding: utf-8 -*-
"""
websocket.routes.products —— 商品拉取接口（供 backend 调用）
==========================================================
本文件用途：提供 websocket 服务的「商品拉取」HTTP 接口，供 backend 服务在触发
「商品同步」时经服务间 HTTP 调用，从拼多多店铺拉取商品列表（需求 15.2 / 15.3）。

- ``POST /products/pull``：按 (shop_id, owner_user_id) 复用拼多多商品列表接口
  （``channel_pdd.api.get_product_list.GetProductList``）拉取在售商品并返回。
- ``POST /products/detail``：按 (shop_id, owner_user_id, goods_id) 复用拼多多商品
  详情接口（``channel_pdd.api.get_product_detail.GetProductDetail``）查询实时规格
  并返回，供 backend「查看商品详情」补充库内未持久化的规格信息（需求 15）。

签名缺失降级（需求 15.3 / 26.2）：商品列表接口依赖 anti-content 签名，当 Cookie
缺少有效签名或接口返回签名失效时，``GetProductList`` 抛 ``AntiContentMissingError``；
本路由捕获后返回**顶层带 ``signature_missing=True`` 标志**的失败响应体，供 backend
``product_sync_client`` 据此终止同步、返回固定中文提示并记系统日志。

接口约定（开发规范 1-3）：HTTP 恒返回 200，业务成败由统一响应体
``{code, success, message, data}`` 表达；签名缺失场景在响应体顶层附加
``signature_missing`` 标志（与 backend product_sync_client 解析约定一致）。
地址经环境变量配置（禁止写死 localhost，规范 21）。

实现约束（开发规范）：导入置顶（规范 51）、中文注释（规范 37）、文件名用下划线
（规范 40）、单文件 ≤500 行（规范 35）、日志禁用 debug（规范 38）。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from channel_pdd.api.get_product_detail import GetProductDetail
from channel_pdd.api.get_product_list import GetProductList
from channel_pdd.core.anti_content import (
    AntiContentMissingError,
    SIGNATURE_MISSING_MESSAGE,
)

logger = logging.getLogger("websocket.routes.products")

# 商品拉取路由：标签便于 OpenAPI 分组；前缀由聚合层添加。
router = APIRouter(tags=["商品拉取"])


class PullProductsRequest(BaseModel):
    """商品拉取请求体（与 backend product_sync_client 约定一致）。"""

    shop_pk: int = Field(..., description="店铺主键 shop.id")
    shop_id: str = Field(..., description="拼多多店铺业务标识")
    owner_user_id: Optional[int] = Field(None, description="店铺归属用户 ID")


class ProductDetailRequest(BaseModel):
    """商品详情查询请求体（与 backend product_sync_client 约定一致）。"""

    shop_id: str = Field(..., description="拼多多店铺业务标识")
    goods_id: str = Field(..., description="拼多多商品业务标识")
    owner_user_id: Optional[int] = Field(None, description="店铺归属用户 ID")


def _success_body(products: list) -> Dict[str, Any]:
    """构造商品拉取成功响应体（统一响应体结构 + products 数据）。

    Args:
        products: 拉取到的商品字典列表。

    Returns:
        统一响应体字典：``{code, success, message, data:{products}}``。
    """
    return {
        "code": 0,
        "success": True,
        "message": "商品拉取成功",
        "data": {"products": products, 'complete': True},
    }


def _signature_missing_body(message: str) -> Dict[str, Any]:
    """构造「签名缺失」失败响应体（顶层附加 signature_missing 标志，需求 15.3）。

    Args:
        message: 中文失败原因。

    Returns:
        统一响应体字典（顶层带 ``signature_missing=True``，data=None）。
    """
    return {
        "code": -1,
        "success": False,
        "message": message,
        "data": None,
        "signature_missing": True,
    }


def _error_body(message: str) -> Dict[str, Any]:
    """构造一般失败响应体（data=None）。

    Args:
        message: 中文失败原因。

    Returns:
        统一响应体字典。
    """
    return {"code": -1, "success": False, "message": message, "data": None}


@router.post("/products/pull", summary="拉取指定店铺的拼多多在售商品")
def pull_products(payload: PullProductsRequest) -> Dict[str, Any]:
    """拉取指定店铺的拼多多在售商品（需求 15.2 / 15.3）。

    复用 ``GetProductList`` 按 (shop_id, owner_user_id) 加载 Cookie 并分页拉取在售
    商品。签名缺失 / 失效时返回顶层带 ``signature_missing`` 标志的失败响应（供 backend
    终止同步并提示用户重新登录）；其它失败返回一般失败响应。本路由不抛异常。

    Args:
        payload: 含 shop_pk / shop_id / owner_user_id 的拉取请求体。

    Returns:
        统一响应体字典（成功含 products；签名缺失附 signature_missing 标志）。
    """
    try:
        fetcher = GetProductList(
            shop_id=payload.shop_id, user_id=payload.owner_user_id
        )
        result = fetcher.fetch_all()
    except AntiContentMissingError as exc:
        # 签名缺失 / 失效：返回带 signature_missing 标志的失败响应（需求 15.3 / 26.2）。
        logger.warning(
            "店铺 shop_id=%s 因签名缺失拉取商品失败", payload.shop_id
        )
        return _signature_missing_body(exc.message or SIGNATURE_MISSING_MESSAGE)
    except Exception as exc:  # noqa: BLE001 - 拉取异常不抛出，规整为失败响应
        logger.error("拉取商品异常: shop_id=%s, %s", payload.shop_id, exc)
        return _error_body("商品拉取失败，请稍后重试")

    if result.get("success") and result.get('complete') is True:
        return _success_body(result.get("products", []))

    # 业务失败（非签名问题）：返回失败原因。
    error_msg = result.get("error_msg") or "商品拉取失败"
    logger.warning("拉取商品业务失败: shop_id=%s, %s", payload.shop_id, error_msg)
    return _error_body(str(error_msg))


@router.post("/products/detail", summary="查询指定商品的拼多多实时详情")
def product_detail(payload: ProductDetailRequest) -> Dict[str, Any]:
    """查询指定商品的拼多多实时详情（规格 / 分类，需求 15）。

    复用 ``GetProductDetail`` 按 (shop_id, owner_user_id) 加载 Cookie 并按 goods_id
    查询商品详情。签名缺失 / 失效时返回顶层带 ``signature_missing`` 标志的失败响应
    （供 backend 提示用户重新登录）；其它失败返回一般失败响应。本路由不抛异常。

    Args:
        payload: 含 shop_id / goods_id / owner_user_id 的详情查询请求体。

    Returns:
        统一响应体字典（成功含 detail；签名缺失附 signature_missing 标志）。
    """
    try:
        fetcher = GetProductDetail(
            shop_id=payload.shop_id, user_id=payload.owner_user_id
        )
        result = fetcher.fetch_detail(payload.goods_id)
    except AntiContentMissingError as exc:
        # 签名缺失 / 失效：返回带 signature_missing 标志的失败响应（需求 26.2）。
        logger.warning(
            "店铺 shop_id=%s 商品 goods_id=%s 因签名缺失查询详情失败",
            payload.shop_id,
            payload.goods_id,
        )
        return _signature_missing_body(exc.message or SIGNATURE_MISSING_MESSAGE)
    except Exception as exc:  # noqa: BLE001 - 查询异常不抛出，规整为失败响应
        logger.error(
            "查询商品详情异常: shop_id=%s, goods_id=%s, %s",
            payload.shop_id,
            payload.goods_id,
            exc,
        )
        return _error_body("商品详情查询失败，请稍后重试")

    if result.get("success"):
        return {
            "code": 0,
            "success": True,
            "message": "商品详情查询成功",
            "data": {"detail": result.get("detail", {})},
        }

    # 业务失败（非签名问题）：返回失败原因。
    error_msg = result.get("error_msg") or "商品详情查询失败"
    logger.warning(
        "查询商品详情业务失败: shop_id=%s, goods_id=%s, %s",
        payload.shop_id,
        payload.goods_id,
        error_msg,
    )
    return _error_body(str(error_msg))


__all__ = ["router"]
