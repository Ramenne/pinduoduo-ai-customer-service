# -*- coding: utf-8 -*-
"""
channel_pdd.api.get_product_list —— 拼多多商品列表查询接口
==========================================================
本文件用途：复用改造参照项目 Customer-Agent-1.2.0
``Channel/pinduoduo/utils/API/product_manager.py``（class ProductManager 的
``get_product_list``），基于本系统拼多多基础请求层 ``BaseRequest`` 查询店铺在售
商品列表，供 backend 经 HTTP 触发的「商品同步」拉取商品（需求 15.2）。

依赖 anti-content 签名（需求 26.1）：anti-content 是拼多多前端风控 JS 在请求时
实时计算并放入请求头的动态签名，并不存在于登录导出的 Cookie 中。对照参照项目
Customer-Agent-1.2.0 ``ProductManager.get_product_list`` 的可用实现，本接口
**不做请求前「签名缺失即拦截」校验**：取不到 anti-content 时以空串照常发起请求，
由拼多多服务端按实际风控结果返回，避免误判为「签名缺失」导致拉取失败。

差异说明（按本系统架构改造）：
- 继承本系统 ``channel_pdd.core.base_request.BaseRequest``（统一请求 / 重试 / 会话
  过期自动重登 / 签名检测），按 (shop_id, user_id) 自数据库加载并解密 Cookie。
- 日志使用本模块标准库 logger（禁用 debug，规范 38）。
- 商品字段解析与参照项目保持一致口径，规整为 backend upsert 所需的字典结构
  （goods_id / goods_name / price / sold_quantity / thumb_url 等）。

实现约束（开发规范）：导入置顶、中文注释、文件名用下划线、单文件 ≤500 行。
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from channel_pdd.core.base_request import BaseRequest

logger = logging.getLogger("channel_pdd.get_product_list")

# 商品列表（推荐 / 在售商品）查询接口地址（拼多多商家后台）。
PRODUCT_LIST_URL: str = "https://mms.pinduoduo.com/latitude/goods/recommendGoods"

# 单页默认条数（与参照项目保持一致）。
_DEFAULT_PAGE_SIZE: int = 20

# 拉取的最大页数保护（避免异常数据导致无限翻页）。
_MAX_PAGES: int = 50

# 翻页间隔（秒）：每翻一页前等待，规避拼多多频率风控（参照项目 product_sync
# 每页 sleep 1.0s 的限流策略，需求 15.2）。
_PAGE_INTERVAL_SECONDS: float = 1.0


class GetProductList(BaseRequest):
    """拼多多店铺商品列表查询。

    按 (shop_id, user_id) 自数据库加载 Cookie 后，分页查询店铺在售商品并规整为
    统一商品字典列表；依赖 anti-content 签名，缺失 / 失效时抛领域异常供上层降级。
    """

    def __init__(
        self,
        shop_id: Optional[str] = None,
        user_id: Optional[int] = None,
        channel_name: str = "pinduoduo",
    ) -> None:
        """构造商品列表查询实例。

        Args:
            shop_id: 拼多多店铺业务标识（与 user_id 同时提供时自数据库加载 Cookie）。
            user_id: 归属用户 ID。
            channel_name: 渠道名称（默认 pinduoduo）。
        """
        super().__init__(shop_id=shop_id, user_id=user_id, channel_name=channel_name)

    def _build_headers(self) -> Dict[str, str]:
        """构造携带 anti-content 的请求头（兼容下划线 / 连字符命名）。"""
        anti_content = self.cookies.get("anti_content") or self.cookies.get(
            "anti-content", ""
        )
        return {
            "accept": "application/json, text/plain, */*",
            "anti-content": anti_content,
            "content-type": "application/json;charset=UTF-8",
            "origin": "https://mms.pinduoduo.com",
            "referer": "https://mms.pinduoduo.com/chat-merchant/index.html",
        }

    def fetch_page(self, page: int = 1, size: int = _DEFAULT_PAGE_SIZE) -> Dict[str, Any]:
        """查询单页商品列表（需求 15.2）。

        对照参照项目 Customer-Agent-1.2.0 ``ProductManager.get_product_list``：
        anti-content 是拼多多前端风控 JS 在请求时实时计算并放入请求头的动态签名，
        并不存在于登录导出的 Cookie 中，故 **不做请求前「签名缺失即拦截」校验**；
        当 Cookie 中取不到 anti-content 时以空串照常发起请求（与参照项目一致），
        由拼多多服务端按实际风控结果返回，避免误判为「签名缺失」而拉取失败。

        Args:
            page: 页码（从 1 开始）。
            size: 每页条数。

        Returns:
            ``{"success": bool, "products": [...], "total": int, "page": int}``；
            业务失败时 ``success=False`` 并附 ``error_msg``。
        """
        data = {"uid": "", "pageNum": page, "pageSize": size}
        result = self.post(
            PRODUCT_LIST_URL,
            json_data=data,
            headers=self._build_headers(),
        )
        if result and result.get("success") is True:
            inner = result.get('result')
            if (not isinstance(inner, dict) or not isinstance(inner.get('onSaleGoods'), list)
                    or type(inner.get('total')) is not int or inner['total'] < 0
                    or any(not isinstance(g, dict) or not g.get('goodsId') for g in inner['onSaleGoods'])):
                return {'success': False, 'error_msg': '商品响应不完整，未更新本地目录'}
            parsed = self._parse_product_list(result)
            return {
                "success": True,
                "products": parsed["products"],
                "total": parsed["total"],
                "page": page,
            }
        error_msg = (result or {}).get("errorMsg") if result else "获取商品列表失败"
        logger.error("获取商品列表失败: shop_id=%s, %s", self.shop_id, error_msg)
        return {"success": False, "error_msg": error_msg, "products": [], "total": 0, "page": page}

    def fetch_all(self, size: int = _DEFAULT_PAGE_SIZE) -> Dict[str, Any]:
        """分页拉取店铺全部在售商品（需求 15.2）。

        先取第一页获得总数，再按需翻页直至取满 ``total`` 或到达页数保护上限；
        任一页业务失败则中止并返回已累计结果与失败信息。

        Args:
            size: 每页条数。

        Returns:
            ``{"success": bool, "products": [...], "total": int}``；
            首页失败时 ``success=False`` 并附 ``error_msg``。
        """
        first = self.fetch_page(page=1, size=size)
        if not first.get("success"):
            return {
                "success": False,
                "error_msg": first.get("error_msg", "获取商品列表失败"),
                "products": [],
                "total": 0,
            }

        products: List[Dict[str, Any]] = list(first.get("products", []))
        total = int(first.get("total", len(products)) or 0)

        page = 2
        while len(products) < total and page <= _MAX_PAGES:
            # 翻页前限流等待，规避频率风控（参照项目每页 sleep 策略，需求 15.2）。
            time.sleep(_PAGE_INTERVAL_SECONDS)
            page_result = self.fetch_page(page=page, size=size)
            if not page_result.get("success"):
                logger.warning(
                    "商品列表第 %d 页拉取失败，提前结束: shop_id=%s", page, self.shop_id
                )
                return {'success': False, 'products': [], 'error_msg': '商品分页未完成，保留原目录'}
            batch = page_result.get("products", [])
            if not batch:
                return {'success': False, 'products': [], 'error_msg': '商品分页提前结束，保留原目录'}
            if page_result.get('total') != total:
                return {'success': False, 'products': [], 'error_msg': '商品数量同步期间发生变化，请稍后重试'}
            products.extend(batch)
            page += 1

        ids = [str(p.get('goods_id')) for p in products]
        if len(ids) != total or len(set(ids)) != total:
            return {'success': False, 'products': [], 'error_msg': '商品分页数量不一致，保留原目录'}
        return {"success": True, "products": products, "total": total, 'complete': True}

    @staticmethod
    def _parse_product_list(response_data: Dict[str, Any]) -> Dict[str, Any]:
        """解析商品列表响应，规整为 backend upsert 所需的统一字典列表。

        Args:
            response_data: 接口响应字典。

        Returns:
            ``{"products": [...], "total": int}``。
        """
        result_data = response_data.get("result", {}) or {}
        goods_list = result_data.get("onSaleGoods", []) or []

        products: List[Dict[str, Any]] = []
        for goods in goods_list:
            if not isinstance(goods, dict):
                continue
            # 价格：取最低在售拼团价（分转元）作为数值落库（Product.price 为
            # Numeric，便于排序 / 计算）；区间展示由前端按需结合销量等另行处理。
            # 注意：不可落库「最低价-最高价」字符串，否则与 Numeric 列类型冲突。
            min_price = goods.get("minOnSaleGroupPrice")
            price_value = round(min_price / 100, 2) if min_price else None

            products.append(
                {
                    "goods_id": goods.get("goodsId"),
                    "goods_name": goods.get("goodsName", ""),
                    "thumb_url": goods.get("thumbUrl", ""),
                    "price": price_value,
                    "sold_quantity": goods.get("soldQuantity", 0),
                }
            )

        return {"products": products, "total": result_data.get("total", len(products))}


__all__ = ["GetProductList", "PRODUCT_LIST_URL"]
