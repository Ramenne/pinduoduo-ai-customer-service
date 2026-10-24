# -*- coding: utf-8 -*-
"""
backend.app.services.transfer_client —— 客服列表查询客户端（调 websocket 服务）
============================================================================
本文件用途：转人工设置需展示某店铺「可分配的人工客服列表」（需求 16.1）。客服列表
依赖店铺 Cookie 实时调用拼多多商家后台接口获取，该能力由 **websocket 服务**承载，
故 backend 经统一服务间 HTTP 客户端调用 websocket 的客服列表接口获取，地址经环境
变量配置（``WEBSOCKET_SERVICE_URL``），**禁止写死 localhost**（规范 21）。

实现要点（任务 19.1 统一）：
- 复用 common 统一服务间 HTTP 客户端 ``common.services.service_client``（规范 36/52）；
- 失败仅记录日志并返回空列表 + 失败标志，不抛异常打断接口主流程（需求 26）。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from common.services import service_client

logger = logging.getLogger(__name__)

# 客服列表查询请求超时（秒）：实时调用拼多多接口，留出合理等待。
_CS_LIST_TIMEOUT_SECONDS: float = 30.0

# websocket 服务客服列表接口相对路径（与 websocket 服务路由约定一致）。
_CS_LIST_PATH: str = "/api/v1/connections/cs-list"


@dataclass
class CsListResult:
    """客服列表查询结果（对 websocket 响应的规整）。

    Attributes:
        ok: 业务层是否成功（websocket 返回 success=true）。
        cs_list: 客服列表（成功时为列表，失败为空列表）。
        message: 失败时的中文原因（成功为空字符串）。
    """

    ok: bool = False
    cs_list: List[Dict[str, Any]] = field(default_factory=list)
    message: str = ""


def fetch_cs_list(shop_id: str, owner_user_id: Optional[int]) -> CsListResult:
    """经 HTTP 调用 websocket 服务查询某店铺可分配的人工客服列表（需求 16.1）。

    无论 websocket 是否可达，本函数都不抛异常：成功返回客服列表，失败返回带中文
    原因的空列表结果，确保不影响接口主流程（需求 26）。

    Args:
        shop_id: 拼多多店铺业务标识。
        owner_user_id: 店铺归属用户 ID（用于 websocket 侧定位 Cookie）。

    Returns:
        规整后的 ``CsListResult``。
    """
    response = service_client.post_json(
        service_client.websocket_base_url(),
        _CS_LIST_PATH,
        {"shop_id": shop_id, "owner_user_id": owner_user_id},
        timeout=_CS_LIST_TIMEOUT_SECONDS,
    )
    if response.success:
        data = response.data or {}
        raw = data.get("list")
        cs_list = [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []
        return CsListResult(ok=True, cs_list=cs_list)

    message = response.message or response.error or "查询客服列表失败"
    logger.warning("查询客服列表失败：shop_id=%s err=%s", shop_id, message)
    return CsListResult(ok=False, message=message)


__all__ = ["CsListResult", "fetch_cs_list"]
