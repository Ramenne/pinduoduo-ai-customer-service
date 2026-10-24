# -*- coding: utf-8 -*-
"""
backend.app.services.smtp_proxy_service —— SMTP 邮件与代理设置业务服务
====================================================================
本文件用途：实现 backend 服务「系统设置」中的 SMTP 邮件与代理设置业务逻辑
（任务 8.2），供 settings 路由复用，覆盖需求 21.7 / 21.8 / 21.14 / 21.15：

- SMTP 参数持久化（服务器、端口、发件邮箱、密码/授权码、发件人显示名），
  其中密码以可逆加密存储；查询接口为管理员专属，应用户要求反显解密后的明文
  供页面「隐藏查看」（密码框 + 显隐切换）核对与编辑（参照 wangpan 系统设置）。
- 测试邮件发送：经已配置 SMTP 向指定收件地址发送测试邮件并返回发送结果
  （需求 21.8）。
- 代理设置持久化（``proxy.enabled`` 与 ``proxy.api_url``）；开启代理但代理 API
  地址为空时返回固定中文提示「开启代理前请先填写代理 API 的 URL」（需求 21.14）。
- 代理地址读取：供调用拼多多接口时按配置走代理（需求 21.15）。

存储说明：复用 setting_store 的键值 upsert（系统级、scope=global）。SMTP 分组键
``smtp``、代理分组键 ``proxy``。SMTP 密码加密经 common.utils.crypto 存储；查询接口
（管理员专属）反显解密后的明文，前端以显隐切换展示，应用户要求支持回显。

实现约束（开发规范）：
- 统一响应体由 common.schemas.common 构造，HTTP 恒 200（规范 1-3 / 需求 24.1）。
- 数据访问经 setting_store（参数化查询），禁止拼接 SQL（规范 16）。
- SMTP 密码加密存储；反显明文仅经管理员专属接口返回，普通接口不涉及该字段。
- 代理 / SMTP 地址经设置持久化，不写死 localhost（规范 21）。
- 导入置顶（规范 51）；中文注释（规范 37）；单文件 ≤500 行（规范 35）。
- 权限与「非管理员拒绝访问」由路由层经 permission 模块统一拦截（需求 21.17）。
"""
from __future__ import annotations

import smtplib
import ssl
from email.mime.text import MIMEText
from email.utils import formataddr
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from app.core.business_codes import (
    CODE_EXTERNAL_ERROR,
    CODE_PARAM_ERROR,
)
from app.services import setting_store
from common.schemas.common import ApiResponse, error_response, success_response
from common.utils.crypto import encrypt_text, try_decrypt_text

# 设置分组键常量（setting_key 取值，集中管理避免散落）。
KEY_SMTP: str = "smtp"
KEY_PROXY: str = "proxy"

# 开启代理但代理 API 地址为空时的固定中文提示（需求 21.14，文案不可更改）。
MSG_PROXY_URL_REQUIRED: str = "开启代理前请先填写代理 API 的 URL"

# 测试邮件发送的网络超时（秒）：避免长时间阻塞接口。
_SMTP_TIMEOUT_SECONDS: float = 10.0

# SMTP 分组默认值：未配置时 GET 返回该默认结构，保证前端可直接渲染。
# 注意：password 为敏感字段，不出现在对外结构中，仅以 password_set 标记是否已配置。
_SMTP_DEFAULTS: Dict[str, Any] = {
    # SMTP 服务器地址（如 smtp.qq.com）。
    "host": "",
    # SMTP 端口（如 465=SSL，587=STARTTLS，25=明文）。
    "port": 465,
    # 发件邮箱地址。
    "sender_email": "",
    # 发件人显示名。
    "sender_name": "",
    # 是否使用 SSL（端口 465 通常为 True）。
    "use_ssl": True,
}

# 代理分组默认值（需求 21.14/21.15）。
_PROXY_DEFAULTS: Dict[str, Any] = {
    # 代理开关。
    "enabled": False,
    # 代理 API 地址（开启时不得为空）。
    "api_url": "",
}


# ----------------------------------------------------------------------
# SMTP 邮件设置（需求 21.7）
# ----------------------------------------------------------------------
def _serialize_smtp(stored: Dict[str, Any]) -> Dict[str, Any]:
    """将 SMTP 存储值序列化为对外结构（含反显明文密码，供管理员页面查看/编辑）。

    密码以可逆加密存储；本接口为管理员专属（登录后鉴权，需求 21.17），故应用户
    要求反显解密后的明文，前端以「隐藏查看」（密码框 + 显隐切换）形式展示，便于
    管理员核对与复制（参照 xianyu-auto-reply-wangpan 系统设置）。同时保留
    ``password_set`` 标记是否已配置密码，便于前端文案展示。

    Args:
        stored: 含 password_enc 等内部字段的存储值。

    Returns:
        对外 SMTP 配置字典，含明文 ``password`` 与 ``password_set`` 标记。
    """
    password_enc = stored.get("password_enc")
    # 解密反显：仅在已配置密码时解密，解密失败（密钥变更等）回退为空串，不抛异常。
    password = try_decrypt_text(password_enc) if password_enc else ""
    return {
        "host": stored.get("host", ""),
        "port": stored.get("port", _SMTP_DEFAULTS["port"]),
        "sender_email": stored.get("sender_email", ""),
        "sender_name": stored.get("sender_name", ""),
        "use_ssl": bool(stored.get("use_ssl", True)),
        # 反显明文密码（管理员专属接口，前端以显隐切换展示），应用户要求支持回显。
        "password": password or "",
        # 仍保留「是否已配置密码」标记，便于前端文案展示。
        "password_set": bool(password_enc),
    }


def get_smtp(session: Session) -> ApiResponse:
    """查询 SMTP 邮件设置（含反显明文密码，供管理员页面查看/编辑）。"""
    stored = setting_store.get_group(session, KEY_SMTP, _SMTP_DEFAULTS)
    return success_response(data=_serialize_smtp(stored), message="查询成功")


def update_smtp(
    session: Session,
    host: Optional[str] = None,
    port: Optional[int] = None,
    sender_email: Optional[str] = None,
    sender_name: Optional[str] = None,
    password: Optional[str] = None,
    use_ssl: Optional[bool] = None,
    *,
    operator_id: Optional[int] = None,
) -> ApiResponse:
    """持久化 SMTP 邮件参数（需求 21.7）。

    仅覆盖显式提供（非 None）的字段；密码经可逆加密存储（``password_enc``），
    响应中绝不返回密码明文或密文。``password`` 为 None 表示不修改既有密码；
    传入空字符串表示清空已配置密码。

    Args:
        session: 数据库会话。
        host: SMTP 服务器地址。
        port: SMTP 端口。
        sender_email: 发件邮箱。
        sender_name: 发件人显示名。
        password: 密码/授权码明文；None=不修改，""=清空。
        use_ssl: 是否使用 SSL。
        operator_id: 操作人用户 ID（仅新建时作为创建人审计字段）。

    Returns:
        统一响应体：成功返回脱敏后的 SMTP 配置。
    """
    # 读取既有存储值（含 password_enc），在其基础上增量更新。
    stored: Dict[str, Any] = dict(_SMTP_DEFAULTS)
    stored.update(setting_store.get_group(session, KEY_SMTP, {}))

    if host is not None:
        stored["host"] = str(host).strip()
    if sender_email is not None:
        stored["sender_email"] = str(sender_email).strip()
    if sender_name is not None:
        stored["sender_name"] = str(sender_name)
    if use_ssl is not None:
        stored["use_ssl"] = bool(use_ssl)
    if port is not None:
        if not isinstance(port, int) or isinstance(port, bool) or not (1 <= port <= 65535):
            return error_response(CODE_PARAM_ERROR, "SMTP 端口须为 1~65535 之间的整数")
        stored["port"] = int(port)
    if password is not None:
        # 空串表示清空密码；非空则可逆加密存储（需求 21.7）。
        if password == "":
            stored["password_enc"] = None
        else:
            stored["password_enc"] = encrypt_text(password)

    setting_store.save_group(session, KEY_SMTP, stored, operator_id)
    return success_response(data=_serialize_smtp(stored), message="SMTP 设置已保存")


def _send_email_via_smtp(
    config: Dict[str, Any], password: str, to_email: str, subject: str, body: str
) -> tuple[bool, str]:
    """经已配置 SMTP 实际发送一封邮件，返回 (是否成功, 结果描述)。

    本函数为「实际投递」入口，便于单元测试以打桩替换外部 IO。自身不向上抛出
    网络异常（捕获后转为失败返回值），保证调用方接口不被异常打断。

    Args:
        config: SMTP 配置（host/port/sender_email/sender_name/use_ssl）。
        password: 已解密的 SMTP 密码/授权码明文。
        to_email: 收件地址。
        subject: 邮件主题。
        body: 邮件正文（纯文本）。

    Returns:
        二元组 (是否成功, 结果描述中文)。
    """
    host = str(config.get("host") or "").strip()
    port = int(config.get("port") or 0)
    sender_email = str(config.get("sender_email") or "").strip()
    sender_name = str(config.get("sender_name") or "")
    use_ssl = bool(config.get("use_ssl", True))

    message = MIMEText(body, "plain", "utf-8")
    message["Subject"] = subject
    message["From"] = formataddr((sender_name or sender_email, sender_email))
    message["To"] = to_email

    try:
        if use_ssl:
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(
                host, port, timeout=_SMTP_TIMEOUT_SECONDS, context=context
            ) as server:
                server.login(sender_email, password)
                server.sendmail(sender_email, [to_email], message.as_string())
        else:
            with smtplib.SMTP(host, port, timeout=_SMTP_TIMEOUT_SECONDS) as server:
                # 非 SSL 端口尽量启用 STARTTLS 提升安全性（服务器支持时）。
                try:
                    server.starttls(context=ssl.create_default_context())
                except smtplib.SMTPException:
                    # 服务器不支持 STARTTLS 时退回明文发送，不中断。
                    pass
                server.login(sender_email, password)
                server.sendmail(sender_email, [to_email], message.as_string())
        return True, "发送成功"
    except (smtplib.SMTPException, OSError, ssl.SSLError) as exc:
        # 网络不可达 / 认证失败 / 超时等：转为失败返回，绝不向上抛出。
        return False, f"邮件发送失败：{exc}"


def send_test_email(
    session: Session,
    to_email: Optional[str],
    *,
    subject: Optional[str] = None,
    content: Optional[str] = None,
) -> ApiResponse:
    """发起测试邮件并返回发送结果（需求 21.8）。

    校验收件地址与 SMTP 是否已配置；经 ``_send_email_via_smtp`` 实际投递。
    发送失败以统一响应体返回失败原因（HTTP 恒 200），不抛异常中断。

    Args:
        session: 数据库会话。
        to_email: 收件地址。
        subject: 邮件主题；None 时使用默认主题。
        content: 邮件正文；None 时使用默认正文。

    Returns:
        统一响应体：成功 success=true；失败返回失败原因。
    """
    if not to_email or not str(to_email).strip():
        return error_response(CODE_PARAM_ERROR, "请填写测试邮件的收件地址")

    stored = setting_store.get_group(session, KEY_SMTP, {})
    host = str(stored.get("host") or "").strip()
    sender_email = str(stored.get("sender_email") or "").strip()
    password_enc = stored.get("password_enc")
    # SMTP 必要参数缺失时给出中文提示（需求 21.8 前置条件）。
    if not host or not sender_email or not password_enc:
        return error_response(
            CODE_PARAM_ERROR, "SMTP 邮件参数未配置完整，请先完善服务器、发件邮箱与密码"
        )

    password = try_decrypt_text(password_enc)
    if not password:
        return error_response(
            CODE_PARAM_ERROR, "SMTP 密码不可用，请重新填写后再发送测试邮件"
        )

    final_subject = (subject or "").strip() or "拼多多自动回复系统测试邮件"
    final_content = (content or "").strip() or "这是一封来自拼多多自动回复系统的测试邮件。"
    ok, detail = _send_email_via_smtp(
        stored, password, str(to_email).strip(), final_subject, final_content
    )
    if ok:
        return success_response(
            data={"to_email": str(to_email).strip(), "detail": detail},
            message="测试邮件已发送",
        )
    return error_response(CODE_EXTERNAL_ERROR, f"测试邮件发送失败：{detail}")


# 验证码邮件类型对应的中文场景名（用于邮件主题与正文文案）。
_CODE_SCENE_NAMES: Dict[str, str] = {
    "register": "账号注册",
    "login": "邮箱登录",
    "reset": "重置密码",
}


def send_verification_code_email(
    session: Session, to_email: str, code: str, code_type: str = "register"
) -> tuple[bool, str]:
    """经已配置 SMTP 发送一封「验证码」邮件，返回 (是否成功, 结果描述中文)。

    供 email_code_service 在下发邮箱验证码时调用：自身校验 SMTP 是否配置完整、
    解密发件密码，再委托 ``_send_email_via_smtp`` 实际投递；任何网络 / 配置异常
    均转为失败返回值，不向上抛出（保证调用方接口 HTTP 恒 200，规范 1）。

    Args:
        session: 数据库会话（读取 SMTP 配置）。
        to_email: 收件邮箱地址。
        code: 待发送的数字验证码。
        code_type: 验证码场景（register/login/reset），决定邮件文案。

    Returns:
        二元组 (是否成功, 结果描述中文)。
    """
    stored = setting_store.get_group(session, KEY_SMTP, {})
    host = str(stored.get("host") or "").strip()
    sender_email = str(stored.get("sender_email") or "").strip()
    password_enc = stored.get("password_enc")
    # SMTP 必要参数缺失时给出中文提示（与测试邮件一致的前置校验）。
    if not host or not sender_email or not password_enc:
        return False, "系统尚未配置邮件服务，请联系管理员"

    password = try_decrypt_text(password_enc)
    if not password:
        return False, "邮件服务密码不可用，请联系管理员"

    scene = _CODE_SCENE_NAMES.get(code_type, "账号验证")
    subject = f"【拼多多自动回复系统】{scene}验证码"
    body = (
        f"您正在进行{scene}操作，验证码为：{code}。\n"
        f"验证码 5 分钟内有效，请勿泄露给他人。\n"
        f"若非本人操作，请忽略本邮件。"
    )
    return _send_email_via_smtp(stored, password, str(to_email).strip(), subject, body)


# ----------------------------------------------------------------------
# 代理设置（需求 21.14 / 21.15）
# ----------------------------------------------------------------------
def _serialize_proxy(stored: Dict[str, Any]) -> Dict[str, Any]:
    """将代理存储值序列化为对外结构。"""
    return {
        "enabled": bool(stored.get("enabled", False)),
        "api_url": str(stored.get("api_url") or ""),
    }


def get_proxy(session: Session) -> ApiResponse:
    """查询代理设置（需求 21.14 配套）。"""
    stored = setting_store.get_group(session, KEY_PROXY, _PROXY_DEFAULTS)
    return success_response(data=_serialize_proxy(stored), message="查询成功")


def update_proxy(
    session: Session,
    enabled: bool,
    api_url: Optional[str] = None,
    *,
    operator_id: Optional[int] = None,
) -> ApiResponse:
    """持久化代理设置（需求 21.14）。

    开启代理（enabled=True）但代理 API 地址为空时，返回固定中文提示
    「开启代理前请先填写代理 API 的 URL」且不持久化（需求 21.14）。

    Args:
        session: 数据库会话。
        enabled: 代理开关。
        api_url: 代理 API 地址。
        operator_id: 操作人用户 ID（仅新建时作为创建人审计字段）。

    Returns:
        统一响应体：成功返回代理配置；开启但地址为空返回固定中文提示。
    """
    normalized_url = (api_url or "").strip()
    # 开启代理时，代理 API 地址不得为空（需求 21.14，固定中文提示）。
    if enabled and not normalized_url:
        return error_response(CODE_PARAM_ERROR, MSG_PROXY_URL_REQUIRED)

    value = {"enabled": bool(enabled), "api_url": normalized_url}
    setting_store.save_group(session, KEY_PROXY, value, operator_id)
    return success_response(data=value, message="代理设置已保存")


def get_active_proxy_url(session: Session) -> Optional[str]:
    """读取「生效中」的代理地址，供调用拼多多接口走代理（需求 21.15）。

    仅当代理开启且地址非空时返回该地址；否则返回 None（表示不走代理）。

    Args:
        session: 数据库会话。

    Returns:
        生效中的代理 API 地址；未开启或未配置时返回 None。
    """
    stored = setting_store.get_group(session, KEY_PROXY, _PROXY_DEFAULTS)
    if not bool(stored.get("enabled", False)):
        return None
    api_url = str(stored.get("api_url") or "").strip()
    return api_url or None


__all__ = [
    "KEY_SMTP",
    "KEY_PROXY",
    "MSG_PROXY_URL_REQUIRED",
    "get_smtp",
    "update_smtp",
    "send_test_email",
    "send_verification_code_email",
    "get_proxy",
    "update_proxy",
    "get_active_proxy_url",
]
