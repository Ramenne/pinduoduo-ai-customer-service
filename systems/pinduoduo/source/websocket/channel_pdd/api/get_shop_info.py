# -*- coding: utf-8 -*-
"""
channel_pdd.api.get_shop_info —— 拼多多店铺信息查询接口
======================================================
本文件用途：复用改造参照项目 Customer-Agent-1.2.0
``Channel/pinduoduo/utils/API/get_shop_info.py``（class GetShopInfo），基于本系统
拼多多基础请求层 ``BaseRequest`` 查询店铺信息（mallId / mallName / mallLogo），
供账号密码登录、Cookie 导入校验等场景获取店铺标识（需求 4.1 / 4.3）。

差异说明（按本系统架构改造）：
- 继承本系统 ``channel_pdd.core.base_request.BaseRequest``；构造时不传
  shop_id / user_id（跳过数据库 Cookie 加载），改由传入的 Cookie 字典 / JSON 注入。
- 日志使用本模块标准库 logger（参照项目使用 self.logger，本系统统一为模块 logger）。

实现约束（开发规范）：导入置顶、中文注释、文件名用下划线。
"""
from __future__ import annotations

import logging
from typing import Any, Optional, Tuple, Union

from channel_pdd.core.base_request import BaseRequest

logger = logging.getLogger("channel_pdd.get_shop_info")

# 店铺信息查询接口地址（拼多多商家后台）。
SHOP_INFO_URL: str = "https://mms.pinduoduo.com/earth/api/merchant/queryMerchantInfoByMallId"


class GetShopInfo(BaseRequest):
    """拼多多店铺信息查询。

    传入已登录的 Cookie（字典或 JSON 字符串）后，调用商家后台接口返回
    ``(shop_id, shop_name, shop_logo)``；失败返回 None。
    """

    def __init__(self, cookies: Optional[Union[dict, str]] = None) -> None:
        """构造店铺信息查询实例。

        Args:
            cookies: 已登录的 Cookie（字典或 JSON 字符串）；为空则不携带 Cookie。
        """
        # 不传 shop_id / user_id：跳过数据库加载，直接使用注入的 Cookie。
        super().__init__()
        if cookies:
            self.update_cookies(cookies)

    def get_shop_info(self) -> Optional[Tuple[Any, Any, Any]]:
        """查询店铺信息。

        Returns:
            成功返回 ``(shop_id, shop_name, shop_logo)`` 三元组；失败返回 None。
        """
        result = self.post(SHOP_INFO_URL, json_data={})
        if result and result.get("success") is True:
            data = result.get("result", {}) or {}
            return data.get("mallId"), data.get("mallName"), data.get("mallLogo")
        error_msg = result.get("errorMsg") if result else "获取店铺信息失败"
        logger.error("获取店铺信息失败: %s", error_msg)
        return None


__all__ = ["GetShopInfo", "SHOP_INFO_URL"]
