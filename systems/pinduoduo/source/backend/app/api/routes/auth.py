# -*- coding: utf-8 -*-
"""
backend.app.api.routes.auth —— 认证接口路由
===========================================
本文件用途：提供 backend 服务的认证相关 REST 接口，满足需求 1：

- ``POST /login``：用户名 + 密码登录，校验通过签发 JWT 并返回
  {token, user}（脱敏）统一响应体；用户名或密码错误返回 success=false、
  message「用户名或密码错误」（需求 1.1/1.2）。
- ``POST /register``：用户自助注册（公开接口），创建用户并自动分配「注册默认
  角色」（非管理员），成功直接签发令牌返回 {token, user}。
- ``POST /logout``：使当前登录令牌失效（需求 1.5）；需携带有效令牌（经
  鉴权依赖），登出后该令牌不可再用。

接口约定（开发规范 1-3）：
- 所有接口 HTTP 恒返回 200，业务成败由统一响应体 {code, success, message,
  data} 表达，由 common.schemas.common 构造。
- 鉴权 / 业务失败经统一异常处理器或 error_response 返回，前端据 success /
  message 弹窗提示。

实现说明：业务逻辑委托 app.services.auth_service，路由层仅负责入参解析与
依赖注入；数据库会话经 common.db.session.get_db 注入。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_token, get_current_user
from app.services import auth_service, setting_service
from common.db.session import get_db
from common.models.user_models import SysUser
from common.schemas.common import ApiResponse, success_response

# 认证路由：标签「认证」便于 OpenAPI 文档分组；前缀由聚合层统一添加。
router = APIRouter(tags=["认证"])


class LoginRequest(BaseModel):
    """登录请求体：用户名 + 密码（+ 滑块验证票据，开启验证码时必填）。"""

    username: str = Field(..., description="登录用户名")
    password: str = Field(..., description="登录明文密码")
    captcha_ticket: str | None = Field(
        None, description="滑块验证通过后签发的一次性票据（开启登录验证码时必填）"
    )


class RegisterRequest(BaseModel):
    """注册请求体：用户名 + 密码 + 邮箱 + 邮箱验证码（自动分配默认角色）。"""

    username: str = Field(..., description="注册用户名（全局唯一）")
    password: str = Field(..., description="注册明文密码（仅用于哈希，不落库明文）")
    email: str | None = Field(None, description="注册邮箱（邮箱验证码归属校验，全局唯一）")
    verification_code: str | None = Field(None, description="邮箱验证码")
    session_id: str | None = Field(None, description="前端会话标识（图形验证码流程使用）")


@router.post("/login", response_model=ApiResponse, summary="用户登录")
def login(
    payload: LoginRequest,
    db: Session = Depends(get_db),
) -> ApiResponse:
    """用户名 + 密码登录（需求 1.1/1.2）。

    成功返回 {token, user}；用户名或密码错误返回 success=false、
    message「用户名或密码错误」，HTTP 恒 200。

    若系统设置开启「登录验证码」（需求 21.6），需先携带滑块验证通过后签发的
    一次性票据 ``captcha_ticket``，校验失败返回中文提示。
    """
    return auth_service.login(
        db, payload.username, payload.password, captcha_ticket=payload.captcha_ticket
    )


@router.post("/register", response_model=ApiResponse, summary="用户注册")
def register(
    payload: RegisterRequest,
    db: Session = Depends(get_db),
) -> ApiResponse:
    """用户自助注册（公开接口，无需登录）。

    需「允许用户注册」开关开启；校验用户名 / 密码 / 邮箱与邮箱验证码后创建新用户
    并自动分配「注册默认角色」（非管理员）。注册成功不自动登录，返回提示由前端
    跳转登录页。开关关闭 / 用户名或邮箱重复 / 验证码错误等返回中文原因，HTTP 恒 200。
    """
    return auth_service.register(
        db,
        payload.username,
        payload.password,
        email=payload.email,
        verification_code=payload.verification_code,
        session_id=payload.session_id,
    )


@router.get("/register/status", response_model=ApiResponse, summary="查询注册开关")
def register_status(db: Session = Depends(get_db)) -> ApiResponse:
    """查询「是否允许用户注册」开关（公开接口，供注册页登录前判断是否开放注册）。

    复用系统设置基础分组的 allow_register（需求 21.6）；开关查询不涉及敏感信息，
    故无需鉴权，登录前即可访问。关闭时前端展示「注册功能已关闭」并引导回登录。
    """
    enabled = setting_service.is_register_allowed(db)
    return success_response(data={"enabled": enabled}, message="查询成功")


@router.post("/logout", response_model=ApiResponse, summary="用户登出")
def logout(
    token: str = Depends(get_current_token),
    # 注入当前用户以确保登出操作处于已登录态（令牌无效时由依赖拒绝）。
    _current_user: SysUser = Depends(get_current_user),
) -> ApiResponse:
    """登出并使当前登录令牌失效（需求 1.5）。

    需携带有效 Bearer 令牌；登出后该令牌的 jti 记入失效登记表，再次使用将被
    判定为「未登录或登录已过期」。
    """
    return auth_service.logout(token)


__all__ = ["router"]
