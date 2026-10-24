# -*- coding: utf-8 -*-
"""
channel_pdd.connection_registry —— 进程内拼多多连接注册表
========================================================
本文件用途：在 websocket 服务进程内维护「店铺 → 已建立的拼多多长连接（PDDChannel）」
的注册表，供以下场景统一定位连接：

- backend 经 HTTP 调用 ``/api/v1/connections/disconnect`` 时，按店铺定位并停止其
  长连接（需求 3.5：店铺停用断开连接）；
- 后续消息处理全链路串联（任务 19.2）时，连接启动后在此登记、停止后注销，使
  断连接口可作用于真实连接。

设计要点：
- 以 ``(owner_user_id, shop_id)`` 为键唯一标识一个店铺连接；同键重复登记以最新
  连接覆盖（避免悬挂旧连接）。
- 仅维护引用与异步停止编排，不感知 PDDChannel 内部实现（鸭子类型，要求其提供
  异步 ``stop()`` 方法）。
- 断开操作幂等：连接不存在时视为「已无连接」直接成功返回，便于 backend 尽力而为
  地通知断连（需求 3.5）。

实现约束（开发规范）：导入置顶（规范 51）、中文注释（规范 37）、文件名用下划线
（规范 40）、单文件 ≤500 行（规范 35）、日志禁用 debug（规范 38）。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger("channel_pdd.connection_registry")

# 连接键类型：(owner_user_id, shop_id)。owner_user_id 可能为空（按 shop_id 兜底）。
_ConnKey = Tuple[Optional[int], str]

# 进程内连接表：键为 (owner_user_id, shop_id)，值为已建立的 PDDChannel 实例。
_CONNECTIONS: Dict[_ConnKey, Any] = {}


def _make_key(shop_id: str, owner_user_id: Optional[int]) -> _ConnKey:
    """构造连接键（owner_user_id 可空）。"""
    return (owner_user_id, str(shop_id))


def register(shop_id: str, owner_user_id: Optional[int], channel: Any) -> None:
    """登记一个已建立的店铺长连接（连接启动后调用）。

    Args:
        shop_id: 拼多多店铺业务标识。
        owner_user_id: 店铺归属用户 ID（可空）。
        channel: 已建立的连接对象（需提供异步 ``stop()``）。
    """
    _CONNECTIONS[_make_key(shop_id, owner_user_id)] = channel
    logger.info("已登记店铺连接：shop_id=%s, owner_user_id=%s", shop_id, owner_user_id)


def unregister(shop_id: str, owner_user_id: Optional[int]) -> None:
    """注销店铺长连接登记（连接停止后调用）。

    Args:
        shop_id: 拼多多店铺业务标识。
        owner_user_id: 店铺归属用户 ID（可空）。
    """
    _CONNECTIONS.pop(_make_key(shop_id, owner_user_id), None)


def get(shop_id: str, owner_user_id: Optional[int]) -> Optional[Any]:
    """按 (owner_user_id, shop_id) 获取已登记的连接（不存在返回 None）。"""
    return _CONNECTIONS.get(_make_key(shop_id, owner_user_id))


def is_connected(shop_id: str, owner_user_id: Optional[int]) -> bool:
    """查询店铺实际连接状态；仅登记了后台任务不代表已经连通。

    Args:
        shop_id: 拼多多店铺业务标识。
        owner_user_id: 店铺归属用户 ID（可空）。

    Returns:
        状态机明确为 connected 时返回 True；否则 False。
    """
    channel = get(shop_id, owner_user_id)
    if channel is None:
        return False
    status = channel.get_connection_status()
    return isinstance(status, dict) and status.get("state") == "connected"


async def disconnect(shop_id: str, owner_user_id: Optional[int]) -> bool:
    """停止并注销指定店铺的长连接（需求 3.5，幂等）。

    连接存在则调用其异步 ``stop()`` 停止并注销；连接不存在视为「已无连接」直接
    返回 True（幂等，便于 backend 尽力而为通知断连）。停止过程中的异常被捕获并
    记录，仍完成注销，不向上抛出。

    Args:
        shop_id: 拼多多店铺业务标识。
        owner_user_id: 店铺归属用户 ID（可空）。

    Returns:
        断开成功（或本就无连接）返回 True；停止过程出错返回 False。
    """
    channel = get(shop_id, owner_user_id)
    if channel is None:
        logger.info(
            "断开连接请求：店铺无活跃连接，视为已断开 shop_id=%s, owner_user_id=%s",
            shop_id,
            owner_user_id,
        )
        return True

    ok = True
    try:
        await channel.stop()
        logger.info("已断开店铺连接：shop_id=%s, owner_user_id=%s", shop_id, owner_user_id)
    except Exception as exc:  # noqa: BLE001 - 停止失败仍需注销，避免悬挂
        logger.error("断开店铺连接出错：shop_id=%s, %s", shop_id, exc)
        ok = False
    finally:
        unregister(shop_id, owner_user_id)
    return ok


def clear() -> None:
    """清空连接表（主要供测试隔离使用）。"""
    _CONNECTIONS.clear()


__all__ = [
    "register",
    "unregister",
    "get",
    "is_connected",
    "disconnect",
    "clear",
]
