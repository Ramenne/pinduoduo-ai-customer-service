# -*- coding: utf-8 -*-
"""
channel_pdd.core.login_state_store —— 店铺登录态标记与账号登录日志
==================================================================
本文件用途：为店铺登录服务（pdd_login）提供「登录态标记」与「账号登录日志记录」
的统一持久化适配，桥接 common 公共库的数据模型与仓储层，满足需求 4.7 / 4.9：

- ``mark_login_state``：按 (shop_id, user_id) 定位 account 记录并更新其登录态
  （unverified/online/offline/relogin，枚举入 sys_dict，需求 4.9 / 5.7）。
- ``record_account_login_log``：将账号登录相关事件写入系统日志（system_log），
  供「Cookie 刷新检测到登录态失效」等场景记录账号登录日志（需求 4.7）。

登录态枚举键（与 common.services.dict_seed_data 的 login_state 一致）：
- ``unverified`` 未验证 / ``online`` 在线 / ``offline`` 离线 / ``relogin`` 需重新登录。

实现约束（开发规范）：
- 数据访问经 common.db.repository 参数化查询，禁止拼接 SQL（规范 16）。
- 时间统一北京时间（规范 17）；导入置顶；中文注释；日志禁用 debug（规范 38）。
"""
from __future__ import annotations

import logging
from typing import Optional

from common.db.repository import Repository
from common.db.session import session_scope
from common.models.log_models import SystemLog
from common.models.shop_models import Account, Shop
from common.utils.time_utils import now_beijing_naive

logger = logging.getLogger("channel_pdd.login_state_store")

# 登录态枚举键（入 sys_dict 的 login_state 类型，需求 4.9 / 5.7）。
LOGIN_STATE_UNVERIFIED: str = "unverified"
LOGIN_STATE_ONLINE: str = "online"
LOGIN_STATE_OFFLINE: str = "offline"
LOGIN_STATE_RELOGIN: str = "relogin"

# 合法登录态集合，用于入参校验，防止写入未登记的枚举值。
_VALID_LOGIN_STATES: frozenset[str] = frozenset(
    {
        LOGIN_STATE_UNVERIFIED,
        LOGIN_STATE_ONLINE,
        LOGIN_STATE_OFFLINE,
        LOGIN_STATE_RELOGIN,
    }
)

# 账号登录日志在 system_log 中的来源模块名。
_LOGIN_LOG_MODULE: str = "pdd_login"


def mark_login_state(shop_id: str, user_id: int, state: str) -> bool:
    """更新指定店铺账号的登录态（需求 4.7 / 4.9）。

    按 (owner_user_id, shop_id) 定位店铺主键，再按 (shop_pk, user_id) 定位 account
    记录并更新其 ``login_state``。登录态为入字典的枚举键，非法值将被拒绝。

    Args:
        shop_id: 拼多多店铺业务标识。
        user_id: 归属用户 ID。
        state: 目标登录态（unverified/online/offline/relogin）。

    Returns:
        更新成功返回 True；登录态非法或账号不存在返回 False。
    """
    if state not in _VALID_LOGIN_STATES:
        logger.error("非法登录态 %s，拒绝写入", state)
        return False

    with session_scope() as session:
        shop = Repository(Shop, session).get_by(owner_user_id=user_id, shop_id=shop_id)
        if shop is None:
            logger.warning("标记登录态失败：未找到店铺 shop_id=%s, user_id=%s", shop_id, user_id)
            return False
        account_repo = Repository(Account, session)
        account = account_repo.get_by(shop_pk=shop.id, user_id=user_id)
        if account is None:
            logger.warning("标记登录态失败：未找到账号 shop_pk=%s, user_id=%s", shop.id, user_id)
            return False
        account_repo.update(account.id, login_state=state)
        logger.info("店铺 shop_id=%s 登录态已标记为 %s", shop_id, state)
        return True


def record_account_login_log(
    content: str,
    level: str = "info",
    user_id: Optional[int] = None,
) -> None:
    """记录账号登录日志到系统日志表 system_log（需求 4.7）。

    供「Cookie 刷新检测到登录态失效」「账号密码登录成功 / 失败」等账号登录事件
    记录日志，便于追溯。写入失败仅记录本地日志，不向上抛出（不中断主流程）。

    Args:
        content: 日志内容（中文）。
        level: 日志级别（info/warning/error，禁用 debug，规范 38）。
        user_id: 关联用户 ID（作为创建人审计字段，可选）。
    """
    try:
        with session_scope() as session:
            Repository(SystemLog, session).create(
                level=level,
                module=_LOGIN_LOG_MODULE,
                content=content,
                log_time=now_beijing_naive(),
                created_by=user_id,
            )
    except Exception as exc:  # noqa: BLE001 - 写日志失败不应中断登录主流程
        logger.error("记录账号登录日志失败: %s", exc)


__all__ = [
    "LOGIN_STATE_UNVERIFIED",
    "LOGIN_STATE_ONLINE",
    "LOGIN_STATE_OFFLINE",
    "LOGIN_STATE_RELOGIN",
    "mark_login_state",
    "record_account_login_log",
]
