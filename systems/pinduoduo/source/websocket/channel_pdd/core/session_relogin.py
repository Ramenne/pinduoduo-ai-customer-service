# -*- coding: utf-8 -*-
"""
channel_pdd.core.session_relogin —— 会话过期检测与自动重登混入
==============================================================
本文件用途：从拼多多基础请求层中拆分出「会话过期（error_code=43001）检测 +
自动刷新 / 重新登录并更新 Cookie」的能力，作为 Mixin 供 BaseRequest 组合
（拆分以满足开发规范 35：单文件 ≤500 行）。

职责（需求 4.6 / 4.7）：
- ``_is_session_expired``：识别响应是否表征「会话已过期」。
- ``set_relogin_func`` / ``_resolve_relogin_func``：注入 / 解析自动重登回调，
  默认惰性接入本系统 pdd_login 模块（后续任务 10.2 提供 ``relogin_pdd``）。
- ``_relogin_and_update_cookies``：会话过期时刷新 / 重登并以可逆加密回写 Cookie。
- ``_apply_new_cookies``：应用新 Cookie 到实例并落库（需求 3.6）。

约定：本 Mixin 依赖宿主类提供 ``shop_id`` / ``user_id`` / ``channel_name`` /
``cookies`` 属性与 ``update_cookies`` 方法（由 BaseRequest 提供）。
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Optional

from channel_pdd.core.credential_store import (
    load_account_credentials,
    update_account_cookies,
)

logger = logging.getLogger("channel_pdd.session_relogin")

# 拼多多会话过期错误码（参照 Customer-Agent 实测）。
SESSION_EXPIRED_ERROR_CODE: int = 43001

# 会话过期错误信息特征关键字。
SESSION_EXPIRED_KEYWORD: str = "会话已过期"


class SessionReloginMixin:
    """会话过期检测与自动重登混入。

    宿主类（BaseRequest）须具备 ``shop_id`` / ``user_id`` / ``channel_name`` /
    ``cookies`` 属性与 ``update_cookies(new_cookies)`` 方法。
    """

    # 重登回调可被外部注入（便于测试与解耦登录模块）。签名：
    #   relogin_func(shop_id, user_id, channel_name, username, password) -> dict | None
    # 返回包含 "cookies" 键的字典表示成功，其余表示失败。
    _relogin_func: Optional[Callable[..., Optional[Dict[str, Any]]]] = None

    # 以下属性 / 方法由宿主类 BaseRequest 提供，此处仅作类型提示占位。
    shop_id: Optional[str]
    user_id: Optional[int]
    channel_name: str
    cookies: Dict[str, Any]

    def update_cookies(self, new_cookies: Any) -> None:  # pragma: no cover - 由宿主实现
        """更新当前实例 Cookie（由宿主类 BaseRequest 实现）。"""
        raise NotImplementedError

    def _is_session_expired(self, response_data: Optional[Dict[str, Any]]) -> bool:
        """检测响应是否表征「会话已过期」（error_code=43001）。

        Args:
            response_data: 接口响应字典。

        Returns:
            会话过期返回 True；否则 False。
        """
        if not response_data:
            return False
        if (
            response_data.get("error_code") == SESSION_EXPIRED_ERROR_CODE
            and SESSION_EXPIRED_KEYWORD in str(response_data.get("error_msg", ""))
        ):
            logger.warning("检测到店铺 shop_id=%s 会话过期", self.shop_id)
            return True
        return False

    @classmethod
    def set_relogin_func(
        cls, func: Optional[Callable[..., Optional[Dict[str, Any]]]]
    ) -> None:
        """注入自动重登回调（解耦登录模块，便于测试）。

        Args:
            func: 重登函数，成功须返回含 "cookies" 键的字典；None 表示清除。
        """
        cls._relogin_func = staticmethod(func) if func is not None else None

    def _resolve_relogin_func(self) -> Optional[Callable[..., Optional[Dict[str, Any]]]]:
        """解析可用的重登回调：优先实例 / 类注入，其次尝试导入 pdd_login。

        Returns:
            可调用的重登函数；不可用时返回 None。
        """
        if self._relogin_func is not None:
            return self._relogin_func
        # 尝试惰性接入本系统登录模块（后续任务 10.2 提供 relogin_pdd）。
        try:
            from channel_pdd import pdd_login  # type: ignore

            candidate = getattr(pdd_login, "relogin_pdd", None)
            if callable(candidate):
                return candidate
        except Exception:  # noqa: BLE001 - 登录模块未就绪时静默降级
            return None
        return None

    def _relogin_and_update_cookies(self) -> bool:
        """会话过期时刷新 / 重新登录并更新 Cookie（需求 4.6 / 4.7）。

        通过注入的重登回调（默认经本系统 pdd_login 模块）获取新 Cookie，成功后
        以可逆加密回写数据库并更新当前实例。登录模块缺位时安全降级（记录日志后
        返回 False，不崩溃）。

        Returns:
            重登并更新 Cookie 成功返回 True；否则 False。
        """
        if self.shop_id is None or self.user_id is None:
            logger.error("缺少 shop_id / user_id，无法自动重登")
            return False

        relogin = self._resolve_relogin_func()
        if relogin is None:
            # 登录模块尚未接入（后续任务 10.2）：安全降级，不中断请求流程。
            logger.warning("未配置自动重登回调，跳过自动重登（登录模块待接入）")
            return False

        credentials = load_account_credentials(
            self.shop_id, self.user_id, self.channel_name
        )
        username = credentials[0] if credentials else None
        password = credentials[1] if credentials else None

        try:
            result = relogin(
                shop_id=self.shop_id,
                user_id=self.user_id,
                channel_name=self.channel_name,
                username=username,
                password=password,
            )
        except Exception as exc:  # noqa: BLE001 - 重登异常不应向上冒泡
            logger.error("自动重登异常: %s", exc)
            return False

        if isinstance(result, dict) and result.get("cookies"):
            if result.get('_session_committed'):
                # 新续期器已CAS提交；不得再次无条件回写覆盖随后到达的新授权。
                self.update_cookies(result['cookies'])
            else:
                self._apply_new_cookies(result['cookies'])
            logger.info("店铺 shop_id=%s 自动重登成功，Cookie 已更新", self.shop_id)
            return True

        logger.error("店铺 shop_id=%s 自动重登失败：未获取到有效 Cookie", self.shop_id)
        return False

    def _apply_new_cookies(self, new_cookies: Any) -> None:
        """应用新 Cookie 到当前实例并加密回写数据库（需求 3.6）。

        Args:
            new_cookies: 新 Cookie（字典或 JSON 字符串）。
        """
        self.update_cookies(new_cookies)
        update_account_cookies(
            self.shop_id, self.user_id, new_cookies, self.channel_name
        )


__all__ = [
    "SessionReloginMixin",
    "SESSION_EXPIRED_ERROR_CODE",
    "SESSION_EXPIRED_KEYWORD",
]
