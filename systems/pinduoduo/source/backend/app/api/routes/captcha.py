# -*- coding: utf-8 -*-
"""
backend.app.api.routes.captcha —— 登录滑块验证码接口路由
========================================================
本文件用途：提供登录前「滑块拼图验证码」的公开 REST 接口（无需鉴权，登录前
即可访问），参照 xianyu-auto-reply-wangpan 登录页的滑块验证形态，但本实现完全
自包含、不依赖任何第三方验证码服务：

- ``GET  /captcha/status``   ：查询「是否启用登录验证码」开关（公开，供登录页
  决定是否展示滑块；复用系统设置基础分组 enable_captcha，需求 21.6）。
- ``POST /captcha/slider/generate``：生成一次滑块验证码挑战（返回背景图、拼图块
  与定位信息）。
- ``POST /captcha/slider/verify``  ：校验用户拖动位移，通过则返回一次性票据，
  登录时回传。
- ``POST /captcha/generate``     ：生成图形字符验证码（注册页人机校验，返回图片
  base64）。
- ``POST /captcha/verify``       ：校验图形字符验证码（通过后即作废）。
- ``POST /captcha/send-email-code``：下发邮箱验证码（注册校验邮箱归属，经 SMTP
  发送，含限频）。

接口约定（开发规范 1-3）：
- 所有接口 HTTP 恒返回 200，业务成败由统一响应体 {code, success, message, data}
  表达。
- 业务逻辑委托 app.services.captcha_service 与 app.services.setting_service，
  路由层仅负责入参解析与依赖注入；数据库会话经 get_db 注入。
- 本组接口为公开接口（登录前调用），不依赖鉴权（与 auth/login、register 一致）。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.services import captcha_service, email_code_service, image_captcha_service, setting_service
from common.db.session import get_db
from common.schemas.common import ApiResponse, error_response, success_response
from app.core.business_codes import CODE_PARAM_ERROR

# 验证码路由：标签「登录验证码」便于 OpenAPI 文档分组；前缀由聚合层统一添加。
router = APIRouter(tags=["登录验证码"])


class SliderVerifyRequest(BaseModel):
    """滑块校验请求体：挑战标识 + 拖动位移。"""

    challenge_id: str = Field(..., description="生成阶段返回的挑战标识")
    distance: float = Field(..., description="拖动的横向位移（像素，相对背景图宽度）")


class ImageCaptchaRequest(BaseModel):
    """图形字符验证码生成请求体：前端会话标识。"""

    session_id: str = Field(..., description="前端会话标识（校验时回传）")


class ImageCaptchaVerifyRequest(BaseModel):
    """图形字符验证码校验请求体：会话标识 + 用户输入。"""

    session_id: str = Field(..., description="生成阶段使用的会话标识")
    captcha_code: str = Field(..., description="用户输入的图形验证码")


class SendEmailCodeRequest(BaseModel):
    """邮箱验证码发送请求体：邮箱 + 场景 + 会话标识。"""

    email: str = Field(..., description="收件邮箱地址")
    type: str = Field("register", description="验证码场景：register/login/reset")
    session_id: str | None = Field(None, description="前端会话标识（图形验证码流程使用）")


@router.get("/captcha/status", response_model=ApiResponse, summary="查询登录验证码开关")
def get_captcha_status(db: Session = Depends(get_db)) -> ApiResponse:
    """查询「是否启用登录验证码」开关（公开接口，供登录页判断是否展示滑块）。

    复用系统设置基础分组的 enable_captcha（需求 21.6）；开关查询不涉及任何敏感
    信息，故无需鉴权，登录前即可访问。
    """
    enabled = setting_service.is_login_captcha_enabled(db)
    return success_response(data={"enabled": enabled}, message="查询成功")


@router.post(
    "/captcha/slider/generate", response_model=ApiResponse, summary="生成滑块验证码"
)
def generate_slider() -> ApiResponse:
    """生成一次滑块拼图验证码挑战（公开接口）。

    返回背景图（含缺口）、拼图块图片与定位信息；前端据此渲染滑块并采集用户拖动
    位移，再调用 verify 接口完成校验。
    """
    data = captcha_service.generate_challenge()
    return success_response(data=data, message="获取成功")


@router.post(
    "/captcha/slider/verify", response_model=ApiResponse, summary="校验滑块验证码"
)
def verify_slider(payload: SliderVerifyRequest) -> ApiResponse:
    """校验用户拖动位移是否对齐缺口（公开接口）。

    通过返回一次性票据 ``ticket``，登录时随登录请求回传；未通过返回中文原因，
    前端据此提示并刷新滑块。HTTP 恒返回 200。
    """
    passed, message, ticket = captcha_service.verify_challenge(
        payload.challenge_id, payload.distance
    )
    if not passed:
        return error_response(CODE_PARAM_ERROR, message)
    return success_response(data={"ticket": ticket}, message=message)


# ----------------------------------------------------------------------
# 图形字符验证码（注册页人机校验，参照 xianyu-auto-reply-wangpan 注册页）
# ----------------------------------------------------------------------
@router.post(
    "/captcha/generate", response_model=ApiResponse, summary="生成图形字符验证码"
)
def generate_image_captcha(payload: ImageCaptchaRequest) -> ApiResponse:
    """生成一张图形字符验证码（公开接口，供注册页人机校验）。

    返回图片 base64 data URL，前端展示并采集用户输入，再调用 verify 校验。
    """
    image, message = image_captcha_service.generate(payload.session_id)
    if image is None:
        return error_response(CODE_PARAM_ERROR, message)
    return success_response(
        data={"captcha_image": image, "session_id": payload.session_id},
        message=message,
    )


@router.post(
    "/captcha/verify", response_model=ApiResponse, summary="校验图形字符验证码"
)
def verify_image_captcha(payload: ImageCaptchaVerifyRequest) -> ApiResponse:
    """校验图形字符验证码（公开接口）；通过后立即作废，未通过返回中文原因。"""
    passed, message = image_captcha_service.verify(
        payload.session_id, payload.captcha_code
    )
    if not passed:
        return error_response(CODE_PARAM_ERROR, message)
    return success_response(data=None, message=message)


@router.post(
    "/captcha/send-email-code", response_model=ApiResponse, summary="发送邮箱验证码"
)
def send_email_code(
    payload: SendEmailCodeRequest, db: Session = Depends(get_db)
) -> ApiResponse:
    """下发邮箱验证码（公开接口，供注册页校验邮箱归属）。

    按场景校验邮箱（注册要求未占用）、限频后经 SMTP 发送 6 位数字验证码；SMTP
    未配置 / 发送失败 / 限频等返回中文原因。HTTP 恒返回 200。
    """
    ok, message = email_code_service.send_code(db, payload.email, payload.type)
    if not ok:
        return error_response(CODE_PARAM_ERROR, message)
    return success_response(data=None, message=message)


__all__ = ["router"]
