# -*- coding: utf-8 -*-
"""
common.schemas 数据结构子包
===========================
本文件为 common 公共库 schemas 子包的初始化文件，统一导出供各服务复用的
数据结构与序列化工具。

schemas 子包提供：
- common：统一响应体 {code, success, message, data} 构造（失败时 success=false、
  message 为中文、data=null）。
- sanitize：敏感字段脱敏序列化机制（移除 / 掩码 Cookie、密码、API 密钥、
  SMTP 密码等敏感字段，支持嵌套结构递归），对应需求 1.6/3.6/8.6/21.7 与
  设计文档 Property 9「敏感字段不外泄」。
"""
from common.schemas.common import (
    DEFAULT_ERROR_MESSAGE,
    DEFAULT_SUCCESS_MESSAGE,
    SUCCESS_CODE,
    ApiResponse,
    build_response,
    error_response,
    is_success_code,
    success_response,
)
from common.schemas.sanitize import (
    DEFAULT_MASK_VALUE,
    DEFAULT_SENSITIVE_FIELDS,
    SafeDTO,
    is_sensitive_field,
    sanitize_sensitive,
)

__all__ = [
    # 统一响应体
    "SUCCESS_CODE",
    "DEFAULT_SUCCESS_MESSAGE",
    "DEFAULT_ERROR_MESSAGE",
    "is_success_code",
    "ApiResponse",
    "build_response",
    "success_response",
    "error_response",
    # 敏感字段脱敏
    "DEFAULT_SENSITIVE_FIELDS",
    "DEFAULT_MASK_VALUE",
    "is_sensitive_field",
    "sanitize_sensitive",
    "SafeDTO",
]
