# -*- coding: utf-8 -*-
"""
test_connection_state_machine —— 连接状态判定与状态机单元测试
============================================================
本文件用途：验证 channel_pdd.core 的连接状态判定与状态机（任务 10.8，需求 5.4/5.5/5.7）：
- 歧义状态 / 明确断开均判定为「需重连」并置「重连中」；明确已连接置「已连接」；
- 重连达上限置「错误」，且无论此前是否实际发起过连接尝试，均恰好生成一条风控日志；
- 状态枚举键与 common 数据字典 conn_state 一致（需求 5.7）。

为不依赖数据库，测试通过注入「风控日志写入回调」对写入次数计数。
"""
from __future__ import annotations

from channel_pdd.core.connection_state_machine import (
    RECONNECT_FAIL_RISK_TYPE,
    ConnectionStateMachine,
    LinkObservation,
    ReconnectPolicy,
    classify_link_observation,
)
from channel_pdd.core.connection_status import (
    ConnectionState,
    ConnectionStatusManager,
)
from common.services.dict_seed_data import DICT_SEED_DATA


class _RiskLogRecorder:
    """风控日志写入回调记录器：记录每次写入的参数，供断言计数。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str, str]] = []

    def __call__(self, shop_id: str, user_id: str, risk_type: str, reason: str) -> None:
        self.calls.append((shop_id, user_id, risk_type, reason))


def _make_machine(max_attempts: int = 3) -> tuple[ConnectionStateMachine, _RiskLogRecorder]:
    """构造一个带记录器与指定重连上限的状态机。"""
    recorder = _RiskLogRecorder()
    machine = ConnectionStateMachine(
        status_manager=ConnectionStatusManager(),
        reconnect_policy=ReconnectPolicy(max_attempts=max_attempts),
        risk_log_sink=recorder,
    )
    return machine, recorder


# ----------------------------------------------------------------------
# classify_link_observation：链路观测 -> 是否需重连
# ----------------------------------------------------------------------
def test_classify_connected_no_reconnect():
    """明确已连接不需要重连。"""
    assert classify_link_observation(LinkObservation.CONNECTED) is False


def test_classify_disconnected_needs_reconnect():
    """明确断开需要重连（需求 5.4）。"""
    assert classify_link_observation(LinkObservation.DISCONNECTED) is True


def test_classify_ambiguous_needs_reconnect():
    """歧义状态需要重连（需求 5.4）。"""
    assert classify_link_observation(LinkObservation.AMBIGUOUS) is True


# ----------------------------------------------------------------------
# 状态转移
# ----------------------------------------------------------------------
def test_connected_sets_connected_state():
    """明确已连接观测置「已连接」。"""
    machine, _ = _make_machine()
    status = machine.handle_link_observation(
        "shop1", "1", "店铺一", LinkObservation.CONNECTED
    )
    assert status.state is ConnectionState.CONNECTED


def test_disconnected_sets_reconnecting():
    """明确断开置「重连中」并累加重连计数。"""
    machine, _ = _make_machine()
    status = machine.handle_link_observation(
        "shop1", "1", "店铺一", LinkObservation.DISCONNECTED
    )
    assert status.state is ConnectionState.RECONNECTING
    assert machine.get_attempts("shop1", "1") == 1


def test_ambiguous_sets_reconnecting():
    """歧义状态置「重连中」。"""
    machine, _ = _make_machine()
    status = machine.handle_link_observation(
        "shop1", "1", "店铺一", LinkObservation.AMBIGUOUS
    )
    assert status.state is ConnectionState.RECONNECTING


def test_reconnect_until_limit_sets_error_with_one_risk_log():
    """重连达上限置「错误」并恰好生成一条风控日志（需求 5.5）。"""
    machine, recorder = _make_machine(max_attempts=3)
    # 前 3 次：重连中（attempts 1,2,3）
    for _ in range(3):
        status = machine.handle_link_observation(
            "shop1", "1", "店铺一", LinkObservation.AMBIGUOUS
        )
        assert status.state is ConnectionState.RECONNECTING
    # 第 4 次观测：已达上限 -> 错误
    status = machine.handle_link_observation(
        "shop1", "1", "店铺一", LinkObservation.AMBIGUOUS
    )
    assert status.state is ConnectionState.ERROR
    assert len(recorder.calls) == 1
    assert recorder.calls[0][2] == RECONNECT_FAIL_RISK_TYPE


def test_error_risk_log_deduplicated():
    """达上限后重复观测不再重复写风控日志（恰好一条）。"""
    machine, recorder = _make_machine(max_attempts=2)
    for _ in range(5):
        machine.handle_link_observation(
            "shop1", "1", "店铺一", LinkObservation.DISCONNECTED
        )
    assert len(recorder.calls) == 1


def test_reach_limit_without_any_attempt_still_logs_once():
    """无论此前是否实际发起过连接尝试，达上限均恰好生成一条风控日志（需求 5.5）。"""
    machine, recorder = _make_machine(max_attempts=0)
    # max_attempts=0：未发起任何重连尝试，直接显式声明达上限
    status = machine.reach_reconnect_limit("shop1", "1", "店铺一")
    assert status.state is ConnectionState.ERROR
    assert len(recorder.calls) == 1
    # 再次调用不重复写
    machine.reach_reconnect_limit("shop1", "1", "店铺一")
    assert len(recorder.calls) == 1


def test_handle_observation_reaches_limit_when_max_attempts_zero():
    """max_attempts=0 时首个需重连观测即达上限并写一条风控日志。"""
    machine, recorder = _make_machine(max_attempts=0)
    status = machine.handle_link_observation(
        "shop1", "1", "店铺一", LinkObservation.DISCONNECTED
    )
    assert status.state is ConnectionState.ERROR
    assert len(recorder.calls) == 1


def test_connected_resets_attempts_and_error_flag():
    """重新连上后重连计数与错误去重标记清零，可再次完整重连并再记一条日志。"""
    machine, recorder = _make_machine(max_attempts=1)
    # 触发一次错误（attempts: 1 -> 第二次达上限）
    machine.handle_link_observation("shop1", "1", "店铺一", LinkObservation.DISCONNECTED)
    machine.handle_link_observation("shop1", "1", "店铺一", LinkObservation.DISCONNECTED)
    assert len(recorder.calls) == 1
    # 恢复已连接
    status = machine.mark_connected("shop1", "1", "店铺一")
    assert status.state is ConnectionState.CONNECTED
    assert machine.get_attempts("shop1", "1") == 0
    # 再次走一轮重连达上限，应再记一条（去重标记已重置）
    machine.handle_link_observation("shop1", "1", "店铺一", LinkObservation.DISCONNECTED)
    machine.handle_link_observation("shop1", "1", "店铺一", LinkObservation.DISCONNECTED)
    assert len(recorder.calls) == 2


def test_mark_connecting_resets_counters():
    """标记连接中重置重连计数。"""
    machine, _ = _make_machine()
    machine.handle_link_observation("shop1", "1", "店铺一", LinkObservation.DISCONNECTED)
    status = machine.mark_connecting("shop1", "1", "店铺一")
    assert status.state is ConnectionState.CONNECTING
    assert machine.get_attempts("shop1", "1") == 0


# ----------------------------------------------------------------------
# 连接状态管理器与字典一致性（需求 5.7）
# ----------------------------------------------------------------------
def test_connection_state_values_match_dict():
    """连接状态枚举值与 common 字典 conn_state 的 dict_key 完全一致（需求 5.7）。"""
    enum_values = {state.value for state in ConnectionState}
    dict_keys = {item[0] for item in DICT_SEED_DATA["conn_state"]}
    assert enum_values == dict_keys


def test_status_manager_tracks_heartbeat_and_count():
    """状态管理器维护心跳时间与已连接计数。"""
    manager = ConnectionStatusManager()
    manager.update_status("shop1", "1", "店铺一", ConnectionState.CONNECTED)
    manager.update_heartbeat("shop1", "1")
    status = manager.get_status("shop1", "1")
    assert status is not None
    assert status.last_heartbeat is not None
    assert manager.get_connected_count() == 1


def test_reconnect_policy_compute_delay_caps_at_max():
    """指数退避延迟封顶 max_delay。"""
    policy = ReconnectPolicy(initial_delay=2.0, max_delay=60.0, backoff_factor=2.0)
    assert policy.compute_delay(0) == 2.0
    assert policy.compute_delay(1) == 4.0
    assert policy.compute_delay(10) == 60.0
