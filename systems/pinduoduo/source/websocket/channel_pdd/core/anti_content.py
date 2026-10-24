# -*- coding: utf-8 -*-
"""
channel_pdd.core.anti_content —— 拼多多 anti-content 签名缺失检测
================================================================
本文件用途：实现「拼多多 anti-content 动态风控签名」的缺失与失效检测，供拼多多
基础请求层（BaseRequest）与依赖签名的接口（商品列表、商品详情、商品卡片发送）
复用，满足需求 26.1 / 26.2：

- 需求 26.1：将依赖 anti-content 签名的接口纳入健壮性兜底处理范围。
- 需求 26.2：当调用上述接口且当前 Cookie 缺少有效签名，或接口返回签名校验失败时，
  应返回明确原因（中文）并记录系统日志，且不中断其它消息处理流程。

设计要点（纯逻辑、无副作用，便于单元 / 属性测试）：
- ``extract_anti_content``：从 Cookie 字典中提取 anti-content 值，兼容
  ``anti_content`` 与 ``anti-content`` 两种命名（参照 Customer-Agent 实现）。
- ``has_valid_anti_content``：判断 Cookie 是否携带「非空」的 anti-content 签名。
- ``is_signature_invalid_response``：从接口响应体识别「签名校验失败 / 风控拦截」。
- ``SIGNATURE_MISSING_MESSAGE``：签名缺失时对用户呈现的固定中文提示（需求 15.3 /
  26.6：提示可通过「账号密码登录」重新获取含完整签名的 Cookie）。
- ``AntiContentMissingError``：签名缺失 / 失效时抛出的领域异常，携带中文原因，
  供上层捕获后转换为统一响应体或记录系统日志。

注意：本模块不发起任何网络请求、不访问数据库，仅做结构化判定，确保可被属性测试
以大量随机输入覆盖。
"""
from __future__ import annotations

from typing import Any, Mapping, Optional

# anti-content 在 Cookie 中可能出现的键名（两种命名变体，均需兼容）。
ANTI_CONTENT_KEYS: tuple[str, ...] = ("anti_content", "anti-content")

# 签名缺失时对用户呈现的固定中文提示（需求 15.3 / 26.6）。
# 引导用户通过「账号密码登录」重新获取含完整签名的 Cookie 以恢复相关能力。
SIGNATURE_MISSING_MESSAGE: str = (
    "当前 Cookie 缺少有效签名，请通过账号密码登录重新获取后再操作"
)

# 拼多多响应中表征「签名校验失败 / 风控拦截」的错误码集合。
# 参照拼多多接口实测：异常风控 / 签名失效时常见的业务错误码；可按需扩充。
SIGNATURE_INVALID_ERROR_CODES: frozenset[int] = frozenset({40001, 54001, 7000041})

# 响应错误信息中表征「签名 / 风控异常」的关键字（中英文兜底匹配）。
_SIGNATURE_INVALID_KEYWORDS: tuple[str, ...] = (
    "anti-content",
    "anti_content",
    "anticontent",
    "签名",
    "验签",
    "风控",
    "风险控制",
    "invalid sign",
    "verify failed",
    "verification failed",
)


class AntiContentMissingError(Exception):
    """anti-content 签名缺失 / 失效领域异常。

    当依赖签名的接口在「Cookie 缺少有效签名」或「接口返回签名校验失败」时，
    由请求层抛出本异常，携带中文原因供上层转换为统一响应体（success=false）
    或记录系统日志（需求 26.2 / 26.5）。

    Attributes:
        message: 中文错误原因（默认采用 ``SIGNATURE_MISSING_MESSAGE``）。
    """

    def __init__(self, message: str = SIGNATURE_MISSING_MESSAGE) -> None:
        """构造签名缺失异常。

        Args:
            message: 中文错误原因；默认采用统一的签名缺失提示文案。
        """
        self.message = message
        super().__init__(message)


def extract_anti_content(cookies: Optional[Mapping[str, Any]]) -> Optional[str]:
    """从 Cookie 字典中提取 anti-content 签名值。

    兼容 ``anti_content`` 与 ``anti-content`` 两种键名（参照 Customer-Agent
    ``send_mallGoodsCard`` 的取值方式）。

    Args:
        cookies: Cookie 字典；为 None 或非映射时视为不含签名。

    Returns:
        提取到的签名字符串（已去除首尾空白）；不存在或为空时返回 None。
    """
    if not isinstance(cookies, Mapping):
        return None
    for key in ANTI_CONTENT_KEYS:
        value = cookies.get(key)
        # 仅接受非空字符串；其它类型 / 空串均视为无效签名。
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def has_valid_anti_content(cookies: Optional[Mapping[str, Any]]) -> bool:
    """判断 Cookie 是否携带「非空」的 anti-content 签名（需求 26.2）。

    Args:
        cookies: Cookie 字典。

    Returns:
        携带非空签名返回 True；缺失或为空返回 False。
    """
    return extract_anti_content(cookies) is not None


def is_signature_invalid_response(response_data: Optional[Mapping[str, Any]]) -> bool:
    """从接口响应体识别「签名校验失败 / 风控拦截」（需求 26.2）。

    判定逻辑（任一命中即视为签名失效）：
    1. 业务错误码命中 ``SIGNATURE_INVALID_ERROR_CODES``（兼容 ``error_code`` /
       ``errorCode`` 两种命名）；
    2. 错误信息（``error_msg`` / ``errorMsg`` / ``message``）包含签名 / 风控关键字。

    Args:
        response_data: 接口响应字典；为 None 或非映射时视为「未识别为签名失效」。

    Returns:
        识别为签名校验失败 / 风控拦截返回 True；否则 False。
    """
    if not isinstance(response_data, Mapping):
        return False

    # 1) 错误码判定（兼容下划线与驼峰命名）。
    for code_key in ("error_code", "errorCode"):
        code = response_data.get(code_key)
        if isinstance(code, int) and code in SIGNATURE_INVALID_ERROR_CODES:
            return True

    # 2) 错误信息关键字判定（中英文兜底）。
    for msg_key in ("error_msg", "errorMsg", "message"):
        msg = response_data.get(msg_key)
        if isinstance(msg, str) and msg:
            lowered = msg.lower()
            if any(keyword in lowered for keyword in _SIGNATURE_INVALID_KEYWORDS):
                return True

    return False


__all__ = [
    "ANTI_CONTENT_KEYS",
    "SIGNATURE_MISSING_MESSAGE",
    "SIGNATURE_INVALID_ERROR_CODES",
    "AntiContentMissingError",
    "extract_anti_content",
    "has_valid_anti_content",
    "is_signature_invalid_response",
]
