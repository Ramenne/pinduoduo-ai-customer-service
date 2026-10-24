# -*- coding: utf-8 -*-
"""
backend.app.core.business_codes —— 业务码常量集中定义
====================================================
本文件用途：集中定义 backend 服务对外统一响应体中使用的「业务码」常量，
供认证、鉴权、权限等模块复用，保证全后端业务码口径一致，并与前端
request.js 的约定对齐（如未登录/登录过期统一为 40100，前端据此跳转登录）。

依据《开发规范》第 1~3 条与需求 1.2/1.4/2.4：
- 所有接口 HTTP 恒返回 200，业务成败通过响应体 {code, success, message, data}
  中的 code / success 表达；
- success 为真当且仅当 code == 0（见 common.schemas.common.is_success_code）。

业务码约定（后端内部统一）：
- 0      ：成功；
- 40001  ：用户名或密码错误（登录失败，需求 1.2）；
- 40100  ：未登录或登录已过期（令牌缺失/无效/过期/已失效，需求 1.4，
           与前端约定一致，前端据此引导重新登录）；
- 40300  ：无访问权限（角色未被授权，需求 2.4）。
"""
from __future__ import annotations

# 成功业务码（与 common.schemas.common.SUCCESS_CODE 一致）。
CODE_SUCCESS: int = 0

# 登录失败：用户名或密码错误（需求 1.2）。
CODE_LOGIN_FAILED: int = 40001

# 未登录或登录已过期：令牌缺失 / 无效 / 过期 / 已主动失效（需求 1.4）。
# 与前端 request.js 约定一致，前端收到该码时引导用户重新登录。
CODE_AUTH_REQUIRED: int = 40100

# 无访问权限：用户角色未被授权访问该接口 / 操作（需求 2.4）。
CODE_FORBIDDEN: int = 40300

# 请求参数非法 / 业务校验不通过（如用户名为空、用户名已存在等）。
CODE_PARAM_ERROR: int = 40000

# 目标资源不存在（如修改 / 停用的用户或角色不存在）。
CODE_NOT_FOUND: int = 40400

# anti-content 签名缺失 / 失效：依赖签名的接口（商品同步等）因 Cookie 缺少有效
# 签名而调用失败（需求 15.3 / 26.2）。
CODE_SIGNATURE_MISSING: int = 42601

# 外部依赖（如 websocket 服务 / 拼多多接口）暂不可用 / 调用失败。
CODE_EXTERNAL_ERROR: int = 42602

# 店铺平台授权失效，与管理后台自身登录过期（40100）分开处理。
CODE_SHOP_AUTH_EXPIRED: int = 42603

# 扫码登录被平台人机验证拦下：需要用户在常用浏览器中自行完成验证后改用其他方式
# 授权。与「扫码超时」「会话不存在」严格区分，不得合并成一个失败码 —— 参照实现与
# 既有账号密码登录正是把这几类混为一谈，才导致前端只能显示"登录超时"。
CODE_QR_VERIFICATION_REQUIRED: int = 42604

# 扫码会话已过期 / 等待超时：二维码失效或到达最长等待时间。
CODE_QR_EXPIRED: int = 42605

# 扫码所得身份与目标店铺不一致：拒绝用另一家店铺的凭据覆盖当前授权。
CODE_SHOP_IDENTITY_MISMATCH: int = 42606

# 数据库备份文件校验失败 / 恢复失败：导入的备份文件格式非法、版本不支持、
# 含未知表 / 列，或恢复过程出错已回滚（需求 21.16）。
CODE_BACKUP_INVALID: int = 42610

# 服务器内部错误：未被业务层捕获的异常（数据库异常、空指针、类型错误等）。
# 由全局异常兜底处理器统一转为「HTTP 200 + 该业务码」，避免向前端抛 500 与堆栈
# （规范 1 / 4：后端一律返回 200，浏览器控制台不应出现报错）。
CODE_SERVER_ERROR: int = 50000


# 常用中文提示文案（统一管理，避免散落各处）。
MSG_LOGIN_FAILED: str = "用户名或密码错误"
MSG_AUTH_REQUIRED: str = "未登录或登录已过期"
MSG_ACCOUNT_DISABLED: str = "账号已被停用，请联系管理员"
MSG_FORBIDDEN: str = "无访问权限"

# 商品同步签名缺失固定中文提示（需求 15.3）。
MSG_SIGNATURE_MISSING: str = (
    "当前 Cookie 缺少有效签名，请通过账号密码登录重新获取后再同步商品"
)

# 扫码登录相关固定中文提示。
MSG_QR_VERIFICATION_REQUIRED: str = (
    "拼多多要求先完成一次人工安全验证才能下发登录二维码。系统不会代为通过该验证，"
    "请在弹出的验证窗口内由您本人完成；通过后即可继续扫码。"
)
MSG_QR_EXPIRED: str = "登录二维码已失效，请重新发起扫码"
MSG_SHOP_IDENTITY_MISMATCH: str = (
    "扫码确认的店铺与目标店铺不一致，已拒绝更新授权，原有凭据未被改动"
)

# 服务器内部错误固定中文提示（不向前端暴露异常堆栈，规范 4）。
MSG_SERVER_ERROR: str = "服务器繁忙，请稍后重试"

# 请求参数校验失败固定中文提示（FastAPI 请求体 / 查询参数校验未通过）。
MSG_PARAM_INVALID: str = "请求参数不合法"


__all__ = [
    "CODE_SUCCESS",
    "CODE_LOGIN_FAILED",
    "CODE_AUTH_REQUIRED",
    "CODE_FORBIDDEN",
    "CODE_PARAM_ERROR",
    "CODE_NOT_FOUND",
    "CODE_SIGNATURE_MISSING",
    "CODE_EXTERNAL_ERROR",
    "CODE_SHOP_AUTH_EXPIRED",
    "CODE_QR_VERIFICATION_REQUIRED",
    "CODE_QR_EXPIRED",
    "CODE_SHOP_IDENTITY_MISMATCH",
    "CODE_BACKUP_INVALID",
    "CODE_SERVER_ERROR",
    "MSG_LOGIN_FAILED",
    "MSG_AUTH_REQUIRED",
    "MSG_ACCOUNT_DISABLED",
    "MSG_FORBIDDEN",
    "MSG_SIGNATURE_MISSING",
    "MSG_QR_VERIFICATION_REQUIRED",
    "MSG_QR_EXPIRED",
    "MSG_SHOP_IDENTITY_MISMATCH",
    "MSG_SERVER_ERROR",
    "MSG_PARAM_INVALID",
]
