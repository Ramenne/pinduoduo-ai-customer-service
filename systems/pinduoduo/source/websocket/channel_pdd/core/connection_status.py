# -*- coding: utf-8 -*-
"""
channel_pdd.core.connection_status —— 连接状态管理器
====================================================
本文件用途：复用改造参照项目 Customer-Agent-1.2.0 的
``core/connection_status.py``，集中维护各店铺 WebSocket 连接状态（需求 5.7 /
5.8），并额外记录「最近心跳时间（北京时间）」供连接状态查询返回（需求 5.8）。

设计要点：
- ``ConnectionState`` 枚举值与 common 数据字典 ``conn_state`` 一致：
  ``connected`` 已连接 / ``connecting`` 连接中 / ``disconnected`` 断开 /
  ``reconnecting`` 重连中 / ``error`` 错误（需求 5.7）。
- ``ConnectionStatusManager`` 线程安全（RLock），仅负责存储与查询连接状态，
  不创建 / 销毁连接、不持有 PDDChannel 实例引用（职责单一）。
- 时间字段统一使用北京时间（开发规范 17、需求 5.8）：
  ``connect_time`` / ``last_connect_time`` / ``last_heartbeat`` 均为北京时间。

协同说明：本管理器同时服务于
- task 10.4 ``PDDChannel``（连接服务）：通过 ``update_status`` / ``record_heartbeat``
  写入状态与心跳，通过 ``get_connection_status`` / ``snapshot`` 对外查询；
- task 10.8 ``ConnectionStateMachine``（状态机）：依赖 ``update_status`` 返回更新后的
  ``ConnectionStatus`` 以驱动状态转移，依赖 ``update_heartbeat`` / ``get_connected_count``。
为兼容两者，``update_status`` 返回更新后的状态对象；``update_heartbeat`` 与
``record_heartbeat`` 互为别名。

实现约束（开发规范）：单文件 ≤500 行、文件名用下划线、导入置顶、注释完善、全中文。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from threading import RLock
from typing import Any, Dict, List, Optional

from common.utils.time_utils import now_beijing, safe_isoformat


class ConnectionState(Enum):
    """连接状态枚举（取值与数据字典 conn_state 一致，需求 5.7）。"""

    DISCONNECTED = "disconnected"  # 断开
    CONNECTING = "connecting"      # 连接中
    CONNECTED = "connected"        # 已连接
    RECONNECTING = "reconnecting"  # 重连中
    ERROR = "error"                # 错误


# 连接状态枚举值 → 中文文案（与 dict_seed_data.conn_state 对齐，便于直接展示）。
CONN_STATE_LABELS: Dict[str, str] = {
    ConnectionState.CONNECTED.value: "已连接",
    ConnectionState.CONNECTING.value: "连接中",
    ConnectionState.DISCONNECTED.value: "断开",
    ConnectionState.RECONNECTING.value: "重连中",
    ConnectionState.ERROR.value: "错误",
}


@dataclass
class ConnectionStatus:
    """单个店铺账号的连接状态信息（时间字段均为北京时间）。

    Attributes:
        shop_id: 拼多多店铺业务标识。
        user_id: 归属用户 ID。
        username: 账号用户名。
        state: 当前连接状态枚举。
        connect_time: 本次进入「已连接」的时间（北京时间）。
        last_connect_time: 最近一次「已连接」的时间（北京时间）。
        last_heartbeat: 最近一次心跳成功时间（北京时间，需求 5.8）。
        error_count: 累计错误次数。
        reconnect_count: 累计重连次数。
        last_error: 最近一次错误原因（中文）。
    """

    shop_id: str
    user_id: str
    username: str
    state: ConnectionState
    connect_time: Optional[datetime] = None
    last_connect_time: Optional[datetime] = None
    last_heartbeat: Optional[datetime] = None
    error_count: int = 0
    reconnect_count: int = 0
    last_error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """序列化为可对外返回的字典（连接状态查询使用，需求 5.8）。

        Returns:
            含状态枚举值、中文文案与最近心跳时间（北京时间 ISO 字符串）的字典。
            为贴合需求 5.8「最近心跳时间」措辞，键名输出为 ``last_heartbeat_time``。
        """
        return {
            "shop_id": self.shop_id,
            "user_id": self.user_id,
            "username": self.username,
            "state": self.state.value,
            "state_label": CONN_STATE_LABELS.get(self.state.value, self.state.value),
            "connect_time": safe_isoformat(self.connect_time),
            "last_connect_time": safe_isoformat(self.last_connect_time),
            "last_heartbeat_time": safe_isoformat(self.last_heartbeat),
            "error_count": self.error_count,
            "reconnect_count": self.reconnect_count,
            "last_error": self.last_error,
        }


class ConnectionStatusManager:
    """连接状态管理器（线程安全）。

    职责边界：
    - 仅负责存储与查询各店铺账号连接状态及最近心跳时间；
    - 不创建 / 销毁连接，不持有 PDDChannel 实例引用；
    - 使用 ``RLock`` 保证多线程 / 多事件循环下的并发安全。
    """

    def __init__(self) -> None:
        """初始化空的连接状态表与可重入锁。"""
        self._connections: Dict[str, ConnectionStatus] = {}
        self._lock = RLock()

    @staticmethod
    def _key(shop_id: str, user_id: Any) -> str:
        """生成连接唯一键（shop_id + user_id）。"""
        return f"{shop_id}_{user_id}"

    def update_status(
        self,
        shop_id: str,
        user_id: Any,
        username: str,
        state: ConnectionState,
        error: Optional[str] = None,
    ) -> ConnectionStatus:
        """更新指定店铺账号的连接状态（时间统一北京时间）。

        Args:
            shop_id: 店铺业务标识。
            user_id: 归属用户 ID。
            username: 账号用户名。
            state: 目标连接状态。
            error: 错误原因（仅在置「错误」状态时记录）。

        Returns:
            更新后的连接状态对象（供状态机驱动转移，需求 5.4 / 5.5）。
        """
        key = self._key(shop_id, user_id)
        with self._lock:
            status = self._connections.get(key)
            if status is None:
                status = ConnectionStatus(
                    shop_id=shop_id,
                    user_id=str(user_id),
                    username=username,
                    state=state,
                )
                self._connections[key] = status

            status.state = state
            status.username = username or status.username

            if state == ConnectionState.CONNECTED:
                now = now_beijing()
                status.connect_time = now
                status.last_connect_time = now
                status.last_error = None
            elif state == ConnectionState.CONNECTING:
                status.connect_time = None
            elif state == ConnectionState.RECONNECTING:
                status.reconnect_count += 1
            elif state == ConnectionState.ERROR:
                status.error_count += 1
                if error:
                    status.last_error = error
            return status

    def update_heartbeat(self, shop_id: str, user_id: Any) -> None:
        """记录最近一次心跳成功时间（北京时间，需求 5.8）。

        Args:
            shop_id: 店铺业务标识。
            user_id: 归属用户 ID。
        """
        key = self._key(shop_id, user_id)
        with self._lock:
            status = self._connections.get(key)
            if status is not None:
                status.last_heartbeat = now_beijing()

    # ``record_heartbeat`` 为 ``update_heartbeat`` 的别名（兼容 PDDChannel 调用）。
    record_heartbeat = update_heartbeat

    def get_status(self, shop_id: str, user_id: Any) -> Optional[ConnectionStatus]:
        """查询指定店铺账号的连接状态。

        Args:
            shop_id: 店铺业务标识。
            user_id: 归属用户 ID。

        Returns:
            连接状态对象；不存在时返回 None。
        """
        with self._lock:
            return self._connections.get(self._key(shop_id, user_id))

    def get_all_status(self) -> List[ConnectionStatus]:
        """查询所有连接状态。

        Returns:
            连接状态对象列表（快照拷贝）。
        """
        with self._lock:
            return list(self._connections.values())

    def get_connected_count(self) -> int:
        """统计当前处于「已连接」的连接数。

        Returns:
            已连接的连接数量。
        """
        with self._lock:
            return sum(
                1
                for status in self._connections.values()
                if status.state == ConnectionState.CONNECTED
            )

    def snapshot(self) -> List[Dict[str, Any]]:
        """导出全部连接状态的可序列化快照（连接状态查询接口使用，需求 5.8）。

        Returns:
            每个连接状态的字典列表（含最近心跳时间，北京时间）。
        """
        with self._lock:
            return [status.to_dict() for status in self._connections.values()]

    def clear_connection(self, shop_id: str, user_id: Any) -> None:
        """清除指定连接记录。

        Args:
            shop_id: 店铺业务标识。
            user_id: 归属用户 ID。
        """
        with self._lock:
            self._connections.pop(self._key(shop_id, user_id), None)

    def clear_all(self) -> None:
        """清空所有连接状态（服务关闭时调用）。"""
        with self._lock:
            self._connections.clear()


__all__ = [
    "ConnectionState",
    "CONN_STATE_LABELS",
    "ConnectionStatus",
    "ConnectionStatusManager",
]
