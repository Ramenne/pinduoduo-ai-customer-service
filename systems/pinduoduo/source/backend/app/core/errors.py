# -*- coding: utf-8 -*-
"""
backend.app.core.errors —— 业务异常与统一响应处理
=================================================
本文件用途：定义 backend 服务的「业务异常」类型与对应的异常处理器，使得
鉴权 / 权限等失败场景在以异常方式中断请求时，仍能返回 **HTTP 200 + 统一
响应体**（开发规范 1：后端接口一律返回 HTTP 200，业务成败由响应体标志字段
表达）。

包含：
- ``BusinessError``：业务异常基类，携带业务码 ``code`` 与中文 ``message``。
- ``AuthError``：鉴权失败异常（未登录 / 登录过期 / 账号停用），默认业务码
  40100（需求 1.4）。
- ``register_exception_handlers(app)``：向 FastAPI 应用注册处理器，将
  ``BusinessError`` 统一转为「状态码 200 + {code, success, message, data}」。

设计说明：FastAPI 依赖（如鉴权依赖）中若校验失败，直接 ``raise AuthError(...)``
即可；本处理器负责落地为统一响应体，避免每个依赖重复构造响应。
"""
from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.business_codes import (
    CODE_AUTH_REQUIRED,
    CODE_PARAM_ERROR,
    CODE_SERVER_ERROR,
    MSG_PARAM_INVALID,
    MSG_SERVER_ERROR,
)
from common.schemas.common import error_response

logger = logging.getLogger("app.core.errors")


class BusinessError(Exception):
    """业务异常基类：携带业务码与中文提示，供统一处理器转为统一响应体。

    Attributes:
        code: 业务码（非 0，表示失败）。
        message: 中文错误信息，前端据此弹窗提示。
    """

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class AuthError(BusinessError):
    """鉴权失败异常：未登录 / 令牌无效或过期 / 账号停用（需求 1.4）。

    默认业务码为 40100（与前端约定一致，前端据此引导重新登录）；账号停用等
    场景可在抛出时传入自定义 message。
    """

    def __init__(self, message: str, code: int = CODE_AUTH_REQUIRED) -> None:
        super().__init__(code=code, message=message)


def register_exception_handlers(app: FastAPI) -> None:
    """向 FastAPI 应用注册业务异常处理器（统一返回 HTTP 200 + 统一响应体）。

    Args:
        app: FastAPI 应用实例。
    """

    @app.exception_handler(BusinessError)
    async def _handle_business_error(_: Request, exc: BusinessError) -> JSONResponse:
        """将业务异常转为「HTTP 200 + 统一失败响应体」（规范 1）。"""
        body = error_response(exc.code, exc.message)
        # status_code 恒为 200：业务成败由响应体 success / code 表达。
        return JSONResponse(status_code=200, content=body.model_dump())

    @app.exception_handler(RequestValidationError)
    async def _handle_validation_error(
        _: Request, exc: RequestValidationError
    ) -> JSONResponse:
        """请求参数校验失败统一转为「HTTP 200 + 参数错误响应体」（规范 1 / 4）。

        FastAPI 默认对请求体 / 查询参数校验失败返回 HTTP 422，违反「一律返回
        200」；这里统一转为业务码 ``CODE_PARAM_ERROR`` 的 200 响应，前端据标志
        字段提示，避免浏览器控制台出现 4xx 报错。
        """
        # 记录首条校验错误便于排障（不外泄到响应，避免暴露内部字段结构）。
        logger.warning("请求参数校验失败: %s", exc.errors())
        body = error_response(CODE_PARAM_ERROR, MSG_PARAM_INVALID)
        return JSONResponse(status_code=200, content=body.model_dump())

    @app.exception_handler(Exception)
    async def _handle_unexpected_error(
        request: Request, exc: Exception
    ) -> JSONResponse:
        """兜底未捕获异常：统一转为「HTTP 200 + 服务器错误响应体」（规范 1 / 4）。

        service 层任何未捕获异常（数据库 IntegrityError、commit 失败、类型转换
        异常等）若不兜底将返回 HTTP 500 + 堆栈，违反「后端一律返回 200、浏览器
        控制台不应出现报错」。这里统一转为业务码 ``CODE_SERVER_ERROR`` 的 200
        响应，并在服务端记录完整异常用于排障（不向前端暴露堆栈）。
        """
        logger.exception(
            "未捕获异常: %s %s -> %s", request.method, request.url.path, exc
        )
        body = error_response(CODE_SERVER_ERROR, MSG_SERVER_ERROR)
        return JSONResponse(status_code=200, content=body.model_dump())


__all__ = [
    "BusinessError",
    "AuthError",
    "register_exception_handlers",
]
