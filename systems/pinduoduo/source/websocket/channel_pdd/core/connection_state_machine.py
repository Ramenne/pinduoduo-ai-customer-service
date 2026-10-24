# -*- coding: utf-8 -*-
"""
channel_pdd.core.connection_state_machine —— 连接状态判定与状态机
==================================================================
本文件用途：在 ``ConnectionStatusManager`` 之上实现「连接状态判定与状态机」，
落地需求 5.4 / 5.5 / 5.7：

- 需求 5.4：WebSocket 连接意外断开，或处于无法确认为「已建立」的**歧义状态**
  （既非明确已连接、也非明确已断开）时，均判定为「需重连」并置「重连中」
  （``RECONNECTING``）；明确已连接置「已连接」（``CONNECTED``）。
- 需求 5.5：自动重连次数未达上限时持续置「重连中」；达到上限时置「错误」
  （``ERROR``）并记录风控日志，且**无论此前是否实际发起过连接尝试，均须恰好
  生成一条风控日志**。
- 需求 5.7：状态枚举（已连接 / 连接中 / 断开 / 重连中 / 错误）经
  ``ConnectionStatusManager`` 维护，枚举键与字典 ``conn_state`` 一致。

设计要点：
- ``classify_link_observation``：纯函数，将「链路观测」（已连接 / 已断开 / 歧义）
  映射为「是否需重连」的判定，便于单测与属性测试（任务 10.9 / Property 11）。
- ``ConnectionStateMachine``：组合 ``ConnectionStatusManager`` 维护状态与计数，
  按重连上限驱动 ``RECONNECTING → ERROR`` 转移；进入 ERROR 时通过「风控日志写入
  回调」恰好写入一条 ``reconnect_fail`` 风控日志（以 ``_error_logged`` 去重，
  保证同一连接同一轮重连周期内仅一条）。
- 风控日志写入解耦为可注入回调（默认经 common 仓储写 ``risk_log`` 表），
  既满足规范 12「SQL 统一管理」，又便于测试在不连库的情况下计数。

实现约束（开发规范）：单文件 ≤500 行、文件名用下划线、导入置顶、注释完善、全中文。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from threading import RLock
from typing import Callable, Dict, Optional, Set

from channel_pdd.core.connection_status import (
    ConnectionState,
    ConnectionStatus,
    ConnectionStatusManager,
)

logger = logging.getLogger("channel_pdd.connection_state_machine")

# 重连失败对应的风控类型键（与 common 字典 risk_type 的 dict_key 一致，需求 13.4）。
RECONNECT_FAIL_RISK_TYPE: str = "reconnect_fail"

# 重连达上限时风控日志的默认触发原因文案（中文）。
RECONNECT_LIMIT_REASON: str = "自动重连次数达到上限，连接置为错误状态"

# 风控日志写入回调签名：sink(shop_id, user_id, risk_type, reason) -> None
RiskLogSink = Callable[[str, str, str, str], None]


class LinkObservation(Enum):
    """链路观测结果（对底层 WebSocket 链路的「明确性」判定，需求 5.4）。

    - CONNECTED：明确已建立（握手 + 鉴权成功、链路可用）。
    - DISCONNECTED：明确已断开（连接关闭 / 出错 / 主动断开）。
    - AMBIGUOUS：歧义状态——既不能确认已建立、也不能确认已断开
      （如握手超时、心跳无响应但未收到关闭帧等）。
    """

    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    AMBIGUOUS = "ambiguous"


@dataclass
class ReconnectPolicy:
    """重连策略（指数退避参数 + 最大重连次数，参照 Customer-Agent 默认值）。

    Attributes:
        max_attempts: 最大重连次数（达到即置「错误」，需求 5.5）。
        initial_delay: 初始退避延迟（秒）。
        max_delay: 退避延迟上限（秒）。
        backoff_factor: 退避倍数。
    """

    max_attempts: int = 5
    initial_delay: float = 2.0
    max_delay: float = 60.0
    backoff_factor: float = 2.0

    def compute_delay(self, attempt: int) -> float:
        """计算第 ``attempt`` 次重连的退避延迟（封顶 ``max_delay``）。

        Args:
            attempt: 重连序号（从 0 起）。

        Returns:
            退避延迟秒数：``min(initial_delay * backoff_factor**attempt, max_delay)``。
        """
        if attempt < 0:
            attempt = 0
        delay = self.initial_delay * (self.backoff_factor ** attempt)
        return min(delay, self.max_delay)


def classify_link_observation(observation: LinkObservation) -> bool:
    """将链路观测映射为「是否需要重连」（需求 5.4 的纯逻辑核心）。

    判定规则：
    - 明确已连接（CONNECTED）→ 无需重连（返回 False）；
    - 明确已断开（DISCONNECTED）或歧义状态（AMBIGUOUS）→ 需重连（返回 True）。

    Args:
        observation: 链路观测结果。

    Returns:
        需重连返回 True；明确已连接返回 False。
    """
    return observation is not LinkObservation.CONNECTED


class ConnectionStateMachine:
    """连接状态机：驱动状态转移并在达重连上限时记录唯一风控日志。

    线程安全：内部以 ``RLock`` 保护重连计数与「已记录错误」集合；状态存储委托
    给线程安全的 ``ConnectionStatusManager``。
    """

    def __init__(
        self,
        status_manager: Optional[ConnectionStatusManager] = None,
        reconnect_policy: Optional[ReconnectPolicy] = None,
        risk_log_sink: Optional[RiskLogSink] = None,
    ) -> None:
        """构造状态机。

        Args:
            status_manager: 连接状态管理器；None 时新建一个独立实例。
            reconnect_policy: 重连策略；None 时使用默认值。
            risk_log_sink: 风控日志写入回调；None 时使用默认（经 common 仓储写库）。
        """
        self._status_manager = status_manager or ConnectionStatusManager()
        self._policy = reconnect_policy or ReconnectPolicy()
        self._risk_log_sink = risk_log_sink or _default_risk_log_sink
        self._lock = RLock()
        # 每个连接当前已发生的重连尝试次数（进入 RECONNECTING 累加）。
        self._attempts: Dict[str, int] = {}
        # 已写入「重连失败」风控日志的连接键集合，保证恰好一条（需求 5.5）。
        self._error_logged: Set[str] = set()

    @property
    def status_manager(self) -> ConnectionStatusManager:
        """暴露底层连接状态管理器（供查询状态 / 心跳更新）。"""
        return self._status_manager

    @property
    def reconnect_policy(self) -> ReconnectPolicy:
        """暴露当前重连策略。"""
        return self._policy

    @staticmethod
    def _key(shop_id: str, user_id: str) -> str:
        """生成内部计数 / 去重用的连接键。"""
        return f"{shop_id}_{user_id}"

    def get_attempts(self, shop_id: str, user_id: str) -> int:
        """查询指定连接当前的重连尝试次数。

        Args:
            shop_id: 店铺业务标识。
            user_id: 归属用户 ID。

        Returns:
            当前重连尝试次数（未重连过为 0）。
        """
        with self._lock:
            return self._attempts.get(self._key(shop_id, user_id), 0)

    def mark_connecting(
        self, shop_id: str, user_id: str, username: str
    ) -> ConnectionStatus:
        """标记为「连接中」并重置该连接的重连计数与错误去重标记。

        Args:
            shop_id: 店铺业务标识。
            user_id: 归属用户 ID。
            username: 店铺 / 账号展示名。

        Returns:
            更新后的连接状态。
        """
        with self._lock:
            self._reset_counters(shop_id, user_id)
            return self._status_manager.update_status(
                shop_id, user_id, username, ConnectionState.CONNECTING
            )

    def mark_connected(
        self, shop_id: str, user_id: str, username: str
    ) -> ConnectionStatus:
        """标记为「已连接」并重置重连计数与错误去重标记（需求 5.4）。

        Args:
            shop_id: 店铺业务标识。
            user_id: 归属用户 ID。
            username: 店铺 / 账号展示名。

        Returns:
            更新后的连接状态。
        """
        with self._lock:
            self._reset_counters(shop_id, user_id)
            return self._status_manager.update_status(
                shop_id, user_id, username, ConnectionState.CONNECTED
            )

    def mark_disconnected(
        self, shop_id: str, user_id: str, username: str
    ) -> ConnectionStatus:
        """标记为「断开」（主动停止场景）并重置重连计数。

        注意：本方法用于「主动停止 / 收到停止信号」的明确断开场景；意外断开 /
        歧义状态应改用 ``handle_link_observation`` 以触发重连判定（需求 5.4）。

        Args:
            shop_id: 店铺业务标识。
            user_id: 归属用户 ID。
            username: 店铺 / 账号展示名。

        Returns:
            更新后的连接状态。
        """
        with self._lock:
            self._reset_counters(shop_id, user_id)
            return self._status_manager.update_status(
                shop_id, user_id, username, ConnectionState.DISCONNECTED
            )

    def handle_link_observation(
        self,
        shop_id: str,
        user_id: str,
        username: str,
        observation: LinkObservation,
    ) -> ConnectionStatus:
        """依据链路观测驱动状态转移（需求 5.4 / 5.5 的状态机入口）。

        - 明确已连接（CONNECTED）→ 置「已连接」并重置重连计数；
        - 已断开 / 歧义（需重连）→ 视当前重连次数：
          - 未达上限：重连计数 +1，置「重连中」；
          - 达到上限：置「错误」并恰好记录一条风控日志（需求 5.5）。

        Args:
            shop_id: 店铺业务标识。
            user_id: 归属用户 ID。
            username: 店铺 / 账号展示名。
            observation: 链路观测结果。

        Returns:
            更新后的连接状态。
        """
        if not classify_link_observation(observation):
            # 明确已连接：恢复已连接并清零计数。
            return self.mark_connected(shop_id, user_id, username)

        with self._lock:
            key = self._key(shop_id, user_id)
            attempts = self._attempts.get(key, 0)
            # 已发起 attempts 次重连仍未连上；若已达上限则置错误。
            if attempts >= self._policy.max_attempts:
                return self._to_error(
                    shop_id, user_id, username, RECONNECT_LIMIT_REASON
                )
            # 未达上限：登记一次重连尝试并置「重连中」。
            self._attempts[key] = attempts + 1
            return self._status_manager.update_status(
                shop_id, user_id, username, ConnectionState.RECONNECTING
            )

    def reach_reconnect_limit(
        self,
        shop_id: str,
        user_id: str,
        username: str,
        reason: str = RECONNECT_LIMIT_REASON,
    ) -> ConnectionStatus:
        """显式声明「重连已达上限」，置「错误」并恰好记录一条风控日志（需求 5.5）。

        本方法用于「重连循环结束后统一判定」的场景：无论此前是否实际发起过连接
        尝试（如 ``max_attempts=0`` 从未重连），调用一次即恰好生成一条风控日志，
        重复调用不再重复写入（由 ``_error_logged`` 去重保证）。

        Args:
            shop_id: 店铺业务标识。
            user_id: 归属用户 ID。
            username: 店铺 / 账号展示名。
            reason: 风控日志触发原因（中文）。

        Returns:
            更新后的连接状态（``ERROR``）。
        """
        with self._lock:
            return self._to_error(shop_id, user_id, username, reason)

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------
    def _to_error(
        self, shop_id: str, user_id: str, username: str, reason: str
    ) -> ConnectionStatus:
        """置「错误」状态并保证恰好写入一条风控日志（需求 5.5）。

        调用方须持有 ``self._lock``。

        Args:
            shop_id: 店铺业务标识。
            user_id: 归属用户 ID。
            username: 店铺 / 账号展示名。
            reason: 风控日志触发原因。

        Returns:
            更新后的连接状态。
        """
        status = self._status_manager.update_status(
            shop_id, user_id, username, ConnectionState.ERROR, error=reason
        )
        key = self._key(shop_id, user_id)
        if key not in self._error_logged:
            # 首次进入错误：恰好写一条风控日志，并打标去重。
            self._error_logged.add(key)
            self._write_risk_log(shop_id, user_id, reason)
        return status

    def _write_risk_log(self, shop_id: str, user_id: str, reason: str) -> None:
        """调用注入的风控日志回调写入一条「重连失败」风控日志（异常不外抛）。

        Args:
            shop_id: 店铺业务标识。
            user_id: 归属用户 ID。
            reason: 触发原因。
        """
        try:
            self._risk_log_sink(
                shop_id, user_id, RECONNECT_FAIL_RISK_TYPE, reason
            )
        except Exception as exc:  # noqa: BLE001 - 写日志失败不应影响状态机
            logger.error("写入重连失败风控日志异常: shop_id=%s, %s", shop_id, exc)

    def _reset_counters(self, shop_id: str, user_id: str) -> None:
        """重置指定连接的重连计数与错误去重标记（调用方须持锁）。"""
        key = self._key(shop_id, user_id)
        self._attempts.pop(key, None)
        self._error_logged.discard(key)


def _default_risk_log_sink(
    shop_id: str, user_id: str, risk_type: str, reason: str
) -> None:
    """默认风控日志写入实现：经 common 仓储向 ``risk_log`` 表写入一条记录。

    先按 ``(owner_user_id, shop_id)`` 定位店铺主键 ``shop_pk``，再写入风控日志，
    时间统一北京时间（规范 17）。所有 SQL 经仓储参数化执行（规范 16）。
    在数据库不可用 / 店铺不存在时记录系统日志并安全降级，不向上抛出。

    Args:
        shop_id: 店铺业务标识。
        user_id: 归属用户 ID。
        risk_type: 风控类型键（此处为 ``reconnect_fail``）。
        reason: 触发原因（中文）。
    """
    # 延迟导入：避免无数据库环境（如纯逻辑测试）下的导入副作用。
    from common.db.repository import Repository, run_in_session
    from common.models.log_models import RiskLog
    from common.models.shop_models import Shop
    from common.utils.time_utils import now_beijing_naive

    def _handler(session: object) -> None:
        shop = Repository(Shop, session).get_by(
            owner_user_id=int(user_id), shop_id=shop_id
        )
        if shop is None:
            logger.warning(
                "写入风控日志时未找到店铺: shop_id=%s, user_id=%s", shop_id, user_id
            )
            return
        Repository(RiskLog, session).create(
            shop_pk=shop.id,
            risk_type=risk_type,
            trigger_reason=reason,
            log_time=now_beijing_naive(),
        )

    try:
        run_in_session(_handler)
    except Exception as exc:  # noqa: BLE001 - 默认实现需对外安全降级
        logger.error("默认风控日志写入失败: shop_id=%s, %s", shop_id, exc)


__all__ = [
    "LinkObservation",
    "ReconnectPolicy",
    "ConnectionStateMachine",
    "classify_link_observation",
    "RECONNECT_FAIL_RISK_TYPE",
    "RECONNECT_LIMIT_REASON",
]
