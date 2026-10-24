# -*- coding: utf-8 -*-
"""
login.cookie_import —— 手动粘贴 Cookie 文本的解析与校验
======================================================
本文件用途：实现「手动粘贴 Cookie 导入」方式下，对用户提交的 Cookie 文本做格式
解析与有效性校验（需求 4.3 / 4.4）。本模块只做「文本 → Cookie 字典」的结构化解析
（纯逻辑，便于单元测试），不发起网络请求；有效性校验（能否据此获取店铺信息）由
``channel_pdd.pdd_login`` 编排调用店铺 / 用户信息接口完成。

支持的 Cookie 文本格式（尽量兼容用户从浏览器复制的多种形态）：
1. JSON 对象字符串：``{"PASS_ID": "xxx", "api_uid": "yyy"}``；
2. JSON 数组（Playwright / 浏览器扩展导出的 ``[{"name":..,"value":..}, ...]``）；
3. 浏览器请求头风格的分号分隔串：``PASS_ID=xxx; api_uid=yyy``。

校验规则（需求 4.4：格式无效返回失败原因）：
- 文本为空 / 解析后无任何键值对 → 视为格式无效；
- 解析得到的键值对中至少需包含一个非空键与非空值。

实现约束（开发规范）：导入置顶、中文注释、文件名用下划线、纯逻辑无副作用。
"""
from __future__ import annotations

import json
import logging
from typing import Dict, Optional, Tuple

logger = logging.getLogger("login.cookie_import")

# Cookie 文本格式无效时对用户呈现的中文提示（需求 4.4）。
COOKIE_FORMAT_INVALID_MESSAGE: str = "Cookie 文本格式无效，请粘贴完整有效的 Cookie"


def parse_cookie_text(cookie_text: Optional[str]) -> Dict[str, str]:
    """将用户粘贴的 Cookie 文本解析为「name->value」字典（兼容多种格式）。

    依次尝试：JSON 对象 → JSON 数组（name/value 列表）→ 分号分隔的请求头串。
    任一解析成功且得到非空键值对即返回；全部失败返回空字典。

    Args:
        cookie_text: 用户粘贴的 Cookie 文本。

    Returns:
        解析得到的 Cookie 字典；无法解析或为空时返回空字典。
    """
    if not cookie_text or not cookie_text.strip():
        return {}

    text = cookie_text.strip()

    # 1) 优先尝试 JSON（对象或数组）。
    parsed = _try_parse_json(text)
    if parsed:
        return parsed

    # 2) 回退到分号分隔的请求头风格串。
    return _parse_header_style(text)


def _try_parse_json(text: str) -> Dict[str, str]:
    """尝试按 JSON 解析 Cookie 文本（兼容对象与 name/value 数组两种结构）。

    Args:
        text: 待解析文本。

    Returns:
        解析得到的 Cookie 字典；非 JSON 或结构不符返回空字典。
    """
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return {}

    # JSON 对象：直接作为 name->value 映射（值统一转字符串）。
    if isinstance(data, dict):
        result: Dict[str, str] = {}
        for key, value in data.items():
            if key is None or value is None:
                continue
            result[str(key)] = str(value)
        return result

    # JSON 数组：浏览器 / 扩展导出的 [{"name":.., "value":..}, ...]。
    if isinstance(data, list):
        result = {}
        for item in data:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            value = item.get("value")
            if name and value is not None:
                result[str(name)] = str(value)
        return result

    return {}


def _parse_header_style(text: str) -> Dict[str, str]:
    """解析分号分隔的请求头风格 Cookie 串（``k1=v1; k2=v2``）。

    Args:
        text: 待解析文本。

    Returns:
        解析得到的 Cookie 字典；无合法键值对返回空字典。
    """
    result: Dict[str, str] = {}
    for segment in text.split(";"):
        segment = segment.strip()
        if not segment or "=" not in segment:
            continue
        # 仅按首个 "=" 切分，兼容值中包含 "=" 的情况。
        key, value = segment.split("=", 1)
        key, value = key.strip(), value.strip()
        if key and value:
            result[key] = value
    return result


def validate_cookie_text(cookie_text: Optional[str]) -> Tuple[bool, Dict[str, str], str]:
    """校验 Cookie 文本格式并返回解析结果（需求 4.3 / 4.4）。

    仅做格式层面的校验（能否解析出非空键值对）；是否能据此获取店铺信息的业务
    校验由上层编排（调用店铺 / 用户信息接口）完成。

    Args:
        cookie_text: 用户粘贴的 Cookie 文本。

    Returns:
        三元组 ``(是否有效, Cookie 字典, 失败原因)``：有效时原因为空串；
        无效时字典为空且原因为中文提示。
    """
    cookies = parse_cookie_text(cookie_text)
    if not cookies:
        logger.warning("Cookie 文本解析为空或格式无效")
        return False, {}, COOKIE_FORMAT_INVALID_MESSAGE
    return True, cookies, ""


__all__ = [
    "COOKIE_FORMAT_INVALID_MESSAGE",
    "parse_cookie_text",
    "validate_cookie_text",
]
