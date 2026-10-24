# -*- coding: utf-8 -*-
"""
channel_pdd.api.get_product_detail —— 拼多多商品详情查询接口
============================================================
本文件用途：复用改造参照项目 Customer-Agent-1.2.0
``Channel/pinduoduo/utils/API/product_manager.py``（class ProductManager 的
``get_product_detail`` / ``_parse_product_detail``），基于本系统拼多多基础请求层
``BaseRequest`` 按 goods_id 查询单个商品的详情（规格 / 分类等），供 backend 经 HTTP
触发的「查看商品详情」实时补充库内未持久化的规格信息（需求 15）。

依赖 anti-content 签名：与商品列表接口一致，anti-content 是拼多多前端风控 JS 在
请求时实时计算并放入请求头的动态签名，并不存在于登录导出的 Cookie 中。对照参照
项目可用实现，本接口 **不做请求前「签名缺失即拦截」校验**：取不到 anti-content 时
以空串照常发起请求，由拼多多服务端按实际风控结果返回，避免误判为「签名缺失」。

差异说明（按本系统架构改造）：
- 继承本系统 ``channel_pdd.core.base_request.BaseRequest``（统一请求 / 重试 / 会话
  过期自动重登），按 (shop_id, user_id) 自数据库加载并解密 Cookie。
- 日志使用本模块标准库 logger（禁用 debug，规范 38）。
- 规格解析与参照项目保持一致口径（skus[].spec 的 parent_name/spec_name 拼接、
  cats 分类补充），规整为统一字典结构供 backend 合并展示。

实现约束（开发规范）：导入置顶、中文注释、文件名用下划线、单文件 ≤500 行。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from channel_pdd.core.base_request import BaseRequest

logger = logging.getLogger("channel_pdd.get_product_detail")

# 商品详情查询接口地址（拼多多商家后台）。
PRODUCT_DETAIL_URL: str = (
    "https://mms.pinduoduo.com/glide/v2/mms/query/commit/on_shop/detail"
)

# 规格信息展示上限（与参照项目保持一致，最多 20 条）。
_MAX_SPECIFICATIONS: int = 20


class GetProductDetail(BaseRequest):
    """拼多多店铺商品详情查询。

    按 (shop_id, user_id) 自数据库加载 Cookie 后，按 goods_id 查询商品详情并解析
    规格 / 分类信息；与列表接口一致，取不到 anti-content 时以空串照常发起请求。
    """

    def __init__(
        self,
        shop_id: Optional[str] = None,
        user_id: Optional[int] = None,
        channel_name: str = "pinduoduo",
    ) -> None:
        """构造商品详情查询实例。

        Args:
            shop_id: 拼多多店铺业务标识（与 user_id 同时提供时自数据库加载 Cookie）。
            user_id: 归属用户 ID。
            channel_name: 渠道名称（默认 pinduoduo）。
        """
        super().__init__(shop_id=shop_id, user_id=user_id, channel_name=channel_name, max_retries=0)

    def _relogin_and_update_cookies(self) -> bool:
        # 查看商品详情不隐式启动漫长的浏览器登录；由店铺授权入口完成恢复。
        return False

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

    def fetch_detail(self, goods_id: Any) -> Dict[str, Any]:
        """按 goods_id 查询单个商品详情（规格 / 分类）。

        对照参照项目 ``ProductManager.get_product_detail``：取不到 anti-content 时
        以空串照常发起请求，由拼多多服务端按实际风控结果返回。

        Args:
            goods_id: 拼多多商品业务标识。

        Returns:
            ``{"success": bool, "detail": {...}}``；业务失败时 ``success=False``
            并附 ``error_msg``。``detail`` 结构：
            ``{"goods_id", "goods_name", "specifications": [str, ...]}``。
        """
        if goods_id is None or not str(goods_id).strip():
            return {"success": False, "error_msg": "商品ID不能为空", "detail": {}}

        data = {"goods_id": goods_id}
        result = self.post(
            PRODUCT_DETAIL_URL,
            json_data=data,
            headers=self._build_headers(),
            timeout=(3, 5),
        )
        if result and result.get("success") is True:
            return {"success": True, "detail": self._parse_product_detail(result)}

        error_msg = ((result or {}).get("errorMsg") or (result or {}).get("error_msg")
                     or "实时规格未能获取，请稍后重试")
        if result and self._is_session_expired(result):
            error_msg = "店铺授权已过期，请在店铺管理重新授权后重试"
        logger.error(
            "获取商品详情失败: shop_id=%s, goods_id=%s, %s",
            self.shop_id,
            goods_id,
            error_msg,
        )
        return {"success": False, "error_msg": error_msg, "detail": {}}

    @staticmethod
    def _parse_product_detail(response_data: Dict[str, Any]) -> Dict[str, Any]:
        """解析商品详情响应，规整为统一字典结构。

        与参照项目 ``_parse_product_detail`` 同口径：
        - 从 ``result.skus[].spec`` 的 parent_name / spec_name 拼接规格组合；
        - 以 ``result.cats`` 分类信息作为规格补充；
        - 规格最多保留 ``_MAX_SPECIFICATIONS`` 条。

        Args:
            response_data: 接口响应字典。

        Returns:
            ``{"goods_id", "goods_name", "specifications": [str, ...]}``。
        """
        result_data = response_data.get("result", {}) or {}

        specifications: List[str] = []
        skus = result_data.get("skus", []) or []
        for sku in skus:
            if not isinstance(sku, dict):
                continue
            specs = sku.get("spec", []) or []
            spec_text: List[str] = []
            for spec_item in specs:
                if not isinstance(spec_item, dict):
                    continue
                parent_name = spec_item.get("parent_name", "")
                spec_name = spec_item.get("spec_name", "")
                if parent_name and spec_name:
                    spec_text.append(f"{parent_name}: {spec_name}")
                elif spec_name:
                    spec_text.append(spec_name)
            if spec_text:
                specifications.append(" | ".join(spec_text))

        # 分类信息作为规格补充（过滤空值后拼接）。
        cats = result_data.get("cats", []) or []
        if isinstance(cats, list):
            valid_cats = [str(cat) for cat in cats if cat]
            if valid_cats:
                specifications.append(f"商品分类: {' > '.join(valid_cats)}")

        return {
            "goods_id": result_data.get("goods_id"),
            "goods_name": result_data.get("goods_name", ""),
            "specifications": specifications[:_MAX_SPECIFICATIONS],
        }


__all__ = ["GetProductDetail", "PRODUCT_DETAIL_URL"]
