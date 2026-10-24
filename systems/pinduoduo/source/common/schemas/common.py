# -*- coding: utf-8 -*-
"""
common.schemas.common —— 统一响应体构造模块
============================================
本文件用途：定义并构造「拼多多自动回复」系统对外的统一响应体。

依据《开发规范》第 1~3 条与需求 24.1/24.2：
- 所有 HTTP 接口恒返回状态码 200（由各服务自行处理），业务成败通过响应体
  字段表达，本模块只负责构造响应体结构，不涉及 HTTP 状态码。
- 统一响应体结构固定为四个字段：{code, success, message, data}。
  * 成功：code=0、success=true、message 默认「成功」、data 为业务数据。
  * 失败：success=false、message 为中文错误信息、data=null、code 为业务码。
- success 的语义约束（对应 Property 21）：success 为真 **当且仅当** code 表示
  成功语义（本系统约定 code==0 即成功语义）。

使用 pydantic 模型实现，便于在 FastAPI 等框架中直接序列化为 JSON。
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

# 成功语义业务码：约定 code == SUCCESS_CODE(0) 即表示业务成功。
SUCCESS_CODE: int = 0

# 默认成功提示文案（中文）。
DEFAULT_SUCCESS_MESSAGE: str = "成功"

# 默认失败提示文案（中文），当调用方未显式提供失败信息时兜底使用。
DEFAULT_ERROR_MESSAGE: str = "操作失败"


def is_success_code(code: int) -> bool:
    """判断给定业务码是否表示成功语义。

    本系统约定业务码为 0（``SUCCESS_CODE``）时表示成功，其余值均表示失败。
    该函数是 success 字段取值的唯一判定来源，确保「success 为真当且仅当
    code 表示成功语义」这一不变式（Property 21）在全系统一致成立。

    Args:
        code: 业务码。

    Returns:
        当 ``code`` 表示成功语义时返回 True，否则返回 False。
    """
    return code == SUCCESS_CODE


class ApiResponse(BaseModel):
    """统一接口响应模型。

    四个字段恒存在：
    - code: 业务码，成功为 0，失败为对应业务码。
    - success: 业务是否成功；恒满足 ``success == is_success_code(code)``。
    - message: 提示信息（中文），失败时为非空错误信息，前端据此弹窗提示。
    - data: 业务数据，失败时恒为 None。

    注意：HTTP 状态码恒为 200，由各服务框架层处理，本模型不承载状态码。
    """

    code: int
    success: bool
    message: str
    data: Any | None = None

    # 允许从 ORM 对象等属性来源构造（与项目其它 schema 保持一致）。
    model_config = ConfigDict(from_attributes=True)


def build_response(code: int, message: str, data: Any | None = None) -> ApiResponse:
    """按业务码构造统一响应体（成败由 code 语义自动推导）。

    success 字段不由调用方直接指定，而是依据 ``is_success_code(code)`` 推导，
    从根本上保证 success 与 code 语义一致（Property 21）。当判定为失败时，
    强制将 data 置为 None，并在 message 为空时回退默认中文失败文案，避免
    出现「失败却带数据」或「失败无提示信息」的非法响应。

    Args:
        code: 业务码，0 表示成功，其余表示失败。
        message: 提示信息（中文）。
        data: 业务数据，仅在成功时保留，失败时强制为 None。

    Returns:
        构造完成的 ``ApiResponse`` 实例。
    """
    success = is_success_code(code)
    if success:
        # 成功：message 为空时回退默认成功文案。
        final_message = message if message else DEFAULT_SUCCESS_MESSAGE
        final_data = data
    else:
        # 失败：message 为空时回退默认中文失败文案；data 强制置空。
        final_message = message if message else DEFAULT_ERROR_MESSAGE
        final_data = None
    return ApiResponse(code=code, success=success, message=final_message, data=final_data)


def success_response(
    data: Any | None = None,
    message: str = DEFAULT_SUCCESS_MESSAGE,
) -> ApiResponse:
    """构造成功响应体。

    成功响应固定 code=0、success=true，message 默认「成功」。

    Args:
        data: 业务数据，默认 None。
        message: 成功提示信息（中文），默认「成功」。

    Returns:
        成功的 ``ApiResponse`` 实例。
    """
    return build_response(code=SUCCESS_CODE, message=message, data=data)


def error_response(code: int, message: str) -> ApiResponse:
    """构造失败响应体。

    失败响应 success=false、data=null，message 为中文错误信息。若误传成功
    语义的 code（如 0），为避免与「失败」语义矛盾，统一回退为通用失败码 -1。

    Args:
        code: 失败业务码；不应为成功语义码（0）。
        message: 中文错误信息。

    Returns:
        失败的 ``ApiResponse`` 实例。
    """
    # 防御：失败响应不允许使用成功语义的业务码，回退通用失败码 -1。
    final_code = code if not is_success_code(code) else -1
    return build_response(code=final_code, message=message, data=None)


__all__ = [
    "SUCCESS_CODE",
    "DEFAULT_SUCCESS_MESSAGE",
    "DEFAULT_ERROR_MESSAGE",
    "is_success_code",
    "ApiResponse",
    "build_response",
    "success_response",
    "error_response",
]
