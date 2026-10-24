# -*- coding: utf-8 -*-
"""
channel_pdd.core.pdd_config —— 拼多多连接重连与心跳配置
======================================================
本文件用途：复用改造参照项目 Customer-Agent-1.2.0 的
``Channel/pinduoduo/core/pdd_config.py`` 与 ``websocket_config.json``，集中管理
拼多多店铺连接服务（PDDChannel）的「自动重连」与「心跳保活」默认参数，满足
需求 5.2 / 5.4 / 5.5：

- ``ReconnectConfig``：指数退避自动重连配置。退避延迟公式为
  ``min(initial_delay * backoff_factor ** attempt, max_delay)``，达到
  ``max_attempts`` 上限后连接置「错误」并记录风控日志（需求 5.5）。
- ``HeartbeatConfig``：心跳保活配置。连接处于「已连接」时按 ``heartbeat_interval``
  周期发送心跳，连续失败超过 ``max_heartbeat_failures`` 视为连接异常（需求 5.2）。

默认值取自参照项目 ``websocket_config.json`` 实测口径：
``reconnect``：``max_attempts=5`` / ``initial_delay=2.0`` / ``max_delay=60.0`` /
``backoff_factor=2.0`` / ``enable_auto_reconnect=true``。

实现约束（开发规范）：单文件 ≤500 行、文件名用下划线、导入置顶、注释完善、全中文。
注意：本模块为纯数据配置，不发起网络请求、不访问数据库，便于单元测试覆盖退避公式。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ReconnectConfig:
    """指数退避自动重连配置（需求 5.4 / 5.5）。

    Attributes:
        max_attempts: 最大重连尝试次数（达上限置「错误」并记录风控日志）。
        initial_delay: 初始重连延迟（秒）。
        max_delay: 单次重连延迟上限（秒），退避延迟封顶值。
        backoff_factor: 退避倍数（每次重试延迟按该倍数指数增长）。
        enable_auto_reconnect: 是否启用自动重连。
    """

    max_attempts: int = 5
    initial_delay: float = 2.0
    max_delay: float = 60.0
    backoff_factor: float = 2.0
    enable_auto_reconnect: bool = True
    continue_after_limit: bool = False  # 真实店铺开启；兼容有界调用/测试。

    def compute_delay(self, attempt: int) -> float:
        """计算第 ``attempt`` 次重连前的退避延迟（秒），封顶 ``max_delay``。

        退避公式（需求 5.4）：``initial_delay * backoff_factor ** attempt``，
        并以 ``max_delay`` 封顶；``attempt`` 从 0 起计。

        Args:
            attempt: 当前重连序号（从 0 起）。

        Returns:
            本次重连前应等待的秒数（非负且不超过 ``max_delay``）。
        """
        # attempt 为负时按 0 处理，避免出现小于 initial_delay 的异常延迟。
        safe_attempt = attempt if attempt > 0 else 0
        delay = self.initial_delay * (self.backoff_factor ** safe_attempt)
        return min(delay, self.max_delay)


@dataclass
class HeartbeatConfig:
    """心跳保活配置（需求 5.2）。

    Attributes:
        enable_heartbeat: 是否启用应用层心跳保活。
        heartbeat_interval: 心跳间隔（秒）。
        heartbeat_timeout: 单次心跳超时（秒）。
        health_check_interval: 健康检查间隔（秒，预留）。
        max_heartbeat_failures: 连续心跳失败上限，超过视为连接异常需重连。
    """

    enable_heartbeat: bool = True
    heartbeat_interval: float = 30.0
    heartbeat_timeout: float = 10.0
    health_check_interval: float = 60.0
    max_heartbeat_failures: int = 3


__all__ = ["ReconnectConfig", "HeartbeatConfig"]
