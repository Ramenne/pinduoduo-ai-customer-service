# -*- coding: utf-8 -*-
"""
backend.app.services.product_spec_codec —— 商品规格 JSON 编解码（公共）
======================================================================
本文件用途：集中实现「商品规格列表 <-> 库内 JSON 文本」的编解码，供
``product_service``（详情查询 / 列表序列化）与 ``product_spec_backfill``
（后台异步补拉落库）共同复用，避免同一逻辑在多处重复实现（规范 36 / 52）。

商品表 ``pdd_product.specifications`` 字段以 JSON 文本存储规格字符串列表
（来自拼多多商品详情）。编解码均做容错：历史脏数据 / None / 空串 / 非法 JSON
统一规整为空，绝不抛出，保证展示与落库的健壮性（需求 15 / 26）。

实现约束：导入置顶（规范 51）、中文注释（规范 37）、文件名用下划线（规范 40）。
"""
from __future__ import annotations

import json
from typing import Any, List, Optional


def decode_specifications(raw: Optional[str]) -> List[str]:
    """将库内存储的规格 JSON 文本解码为字符串列表（容错）。

    Args:
        raw: 库内规格 JSON 文本；None / 空串 / 非法 JSON 均视为无规格。

    Returns:
        规格字符串列表；解析失败时返回空列表。
    """
    if not raw or not str(raw).strip():
        return []
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return []
    if not isinstance(parsed, list):
        return []
    # 仅保留可展示的字符串项（兼容历史脏数据）。
    return [str(item) for item in parsed if item is not None]


def encode_specifications(specifications: Any) -> Optional[str]:
    """将规格字符串列表编码为库内存储的 JSON 文本（容错）。

    Args:
        specifications: 规格字符串列表；非列表 / 空列表均视为无规格返回 None。

    Returns:
        JSON 文本（ensure_ascii=False 保留中文）；无有效规格时返回 None。
    """
    if not isinstance(specifications, list) or not specifications:
        return None
    items = [str(item) for item in specifications if item is not None]
    if not items:
        return None
    return json.dumps(items, ensure_ascii=False)


__all__ = ["decode_specifications", "encode_specifications"]
