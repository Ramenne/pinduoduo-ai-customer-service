# -*- coding: utf-8 -*-
"""
websocket.engine.risk_control —— 风控频率限制判断与风控日志生成（纯逻辑）
======================================================================
本文件用途：实现 websocket 自动回复引擎运行时的「风控频率限制判断」纯逻辑，
供 reply_engine 决策链复用，满足需求 13.2：

- 需求 13.2：某会话在统计窗口内的回复次数达到配置上限时，自动回复引擎应暂停
  对该会话的自动回复并记录风控日志。本系统在「单会话」基础上同时支持「单店铺」
  维度的窗口频率上限判断（与 backend RiskRule 的 session_reply_limit /
  shop_reply_limit / window_seconds 配置一致）。

判定模型（对应 Property 18：风控频率限制）：
- 给定统计窗口 window_seconds 与回复频率上限 N（单会话 N=session_reply_limit、
  单店铺 N=shop_reply_limit），以及窗口内已发生的回复时间序列；
- 在准备发送「下一条」自动回复前，统计「参考时刻 now 所在窗口内」的历史回复
  次数 count；当 count ≥ N 时判定达上限，暂停回复并生成一条风控日志数据
  （风控类型 ``frequency_limit``）。

设计要点：
- 时间口径统一为北京时间，复用 common.utils.time_utils（now_beijing / safe_isoformat）；
- 上限为空（None）表示该维度不限制；窗口为空（None 或 ≤0）表示不按时间窗口截断，
  统计全部传入的回复时间；
- 先判「单会话」再判「单店铺」，任一维度达上限即暂停，并只生成一条风控日志
  （携带触发原因与达上限的维度信息）；
- 纯逻辑、无 I/O：风控规则配置与回复时间序列由上层（从持久化层 / 运行时计数）
  传入，本模块仅做计数与判定，不访问数据库；生成的风控日志数据由上层落库。

实现约束（开发规范）：
- 导入置顶（规范 51）；中文注释（规范 37）；单文件 ≤500 行（规范 35）；
  文件名用下划线（规范 40）；全中文（规范 50）；复用共通库 time_utils（规范 52）。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Iterable, Optional

from common.utils.time_utils import BEIJING_TZ, now_beijing, safe_isoformat

# 风控类型常量（与 backend 数据字典 risk_type 一致，需求 13.4）。
RISK_TYPE_FREQUENCY_LIMIT: str = "frequency_limit"  # 频率限制

# 频率维度常量：单会话 / 单店铺。
SCOPE_SESSION: str = "session"  # 单会话维度
SCOPE_SHOP: str = "shop"  # 单店铺维度


# ----------------------------------------------------------------------
# 风控日志数据（纯数据结构，达上限时生成，供上层落库到 risk_log 表）
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class RiskLogData:
    """风控日志数据结构（与 common.models.log_models.RiskLog 字段对齐）。

    Attributes:
        shop_pk: 关联店铺主键 shop.id。
        risk_type: 风控类型（频率限制固定为 ``frequency_limit``，需求 13.4）。
        trigger_reason: 触发原因（中文，描述达上限的维度、上限值与窗口）。
        log_time: 日志时间（北京时间，带时区）。
    """

    shop_pk: int
    risk_type: str
    trigger_reason: Optional[str]
    log_time: datetime

    def as_dict(self) -> Dict[str, Any]:
        """转为可直接落库 / 序列化的字典（log_time 为北京时间 ISO 串）。

        Returns:
            含 shop_pk / risk_type / trigger_reason / log_time 的字典。
        """
        return {
            "shop_pk": self.shop_pk,
            "risk_type": self.risk_type,
            "trigger_reason": self.trigger_reason,
            "log_time": safe_isoformat(self.log_time),
        }


# ----------------------------------------------------------------------
# 频率检查结果（纯数据结构）
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class FrequencyCheckResult:
    """风控频率检查结果。

    Attributes:
        blocked: 是否达上限需暂停自动回复（达上限为 True）。
        scope: 触发达上限的维度（session / shop）；未达上限为 None。
        reply_count: 触发维度在窗口内的历史回复次数；未达上限为 None。
        limit: 触发维度配置的回复频率上限；未达上限为 None。
        risk_log: 达上限时生成的风控日志数据；未达上限为 None。
    """

    blocked: bool
    scope: Optional[str] = None
    reply_count: Optional[int] = None
    limit: Optional[int] = None
    risk_log: Optional[RiskLogData] = None


# ----------------------------------------------------------------------
# 时刻归一辅助
# ----------------------------------------------------------------------
def _to_beijing(dt: datetime) -> datetime:
    """将 datetime 归一到北京时间（带时区），朴素时间按北京时间口径补齐时区。

    Args:
        dt: 待归一的时刻。

    Returns:
        北京时间口径的 datetime（带 BEIJING_TZ 时区）。
    """
    if dt.tzinfo is None:
        # 朴素时间按北京时间口径解释，补齐北京时区
        return dt.replace(tzinfo=BEIJING_TZ)
    # 带时区时间统一换算到北京时间，保证比较口径一致
    return dt.astimezone(BEIJING_TZ)


# ----------------------------------------------------------------------
# 窗口内回复计数（纯函数）
# ----------------------------------------------------------------------
def count_within_window(
    reply_times: Optional[Iterable[datetime]],
    window_seconds: Optional[int],
    *,
    now: Optional[datetime] = None,
) -> int:
    """统计「参考时刻所在统计窗口内」的回复次数（纯函数）。

    窗口定义为 ``(now - window_seconds, now]``：回复时刻晚于窗口起点且不晚于参考
    时刻即计入。当窗口为空（None 或 ≤0）时不做时间截断，统计全部不晚于 now 的
    回复时刻；晚于 now 的「未来」时刻一律不计入。

    Args:
        reply_times: 历史回复时刻序列（可为朴素或带时区，统一按北京时间口径比较）；
            None 视为空序列。
        window_seconds: 统计窗口秒数（None 或 ≤0 表示不按时间窗口截断）。
        now: 参考时刻；默认取当前北京时间。可传入用于测试。

    Returns:
        窗口内的回复次数（非负整数）。
    """
    if not reply_times:
        return 0

    reference = _to_beijing(now) if now is not None else now_beijing()

    # 是否启用时间窗口截断：窗口为空或非正数时统计全部历史回复
    use_window = isinstance(window_seconds, int) and window_seconds > 0
    threshold = (
        reference.timestamp() - float(window_seconds) if use_window else None
    )

    count = 0
    for raw in reply_times:
        moment = _to_beijing(raw)
        moment_ts = moment.timestamp()
        # 未来时刻不计入（防止异常数据放大计数）
        if moment_ts > reference.timestamp():
            continue
        # 启用窗口时，仅统计窗口起点之后（严格大于）的回复
        if threshold is not None and moment_ts <= threshold:
            continue
        count += 1
    return count


# ----------------------------------------------------------------------
# 单维度上限判断（纯函数）
# ----------------------------------------------------------------------
def is_limit_exceeded(reply_count: int, limit: Optional[int]) -> bool:
    """判断窗口内回复次数是否达到（或超过）配置上限（纯函数）。

    上限为空（None）表示该维度不限制，恒不达上限。达上限语义为「次数 ≥ 上限」：
    当窗口内已有 ``limit`` 次回复时，下一条回复即被暂停（需求 13.2 / Property 18）。

    Args:
        reply_count: 窗口内的历史回复次数。
        limit: 配置的回复频率上限（None 表示不限制）。

    Returns:
        达上限返回 True；否则 False。
    """
    if limit is None:
        return False
    return reply_count >= limit


def _build_reason(scope: str, reply_count: int, limit: int, window_seconds: Optional[int]) -> str:
    """构造风控日志触发原因（中文）。

    Args:
        scope: 触发维度（session / shop）。
        reply_count: 窗口内回复次数。
        limit: 配置上限。
        window_seconds: 统计窗口秒数（None 表示不限窗口）。

    Returns:
        中文触发原因描述。
    """
    scope_label = "单会话" if scope == SCOPE_SESSION else "单店铺"
    if isinstance(window_seconds, int) and window_seconds > 0:
        window_label = f"{window_seconds}秒统计窗口内"
    else:
        window_label = "统计窗口内"
    return (
        f"{scope_label}{window_label}回复次数已达上限"
        f"（{reply_count}/{limit}），暂停自动回复"
    )


# ----------------------------------------------------------------------
# 风控频率限制综合判断（需求 13.2 / Property 18）
# ----------------------------------------------------------------------
def check_reply_frequency(
    shop_pk: int,
    *,
    session_reply_times: Optional[Iterable[datetime]] = None,
    shop_reply_times: Optional[Iterable[datetime]] = None,
    session_reply_limit: Optional[int] = None,
    shop_reply_limit: Optional[int] = None,
    window_seconds: Optional[int] = None,
    enabled: bool = True,
    now: Optional[datetime] = None,
) -> FrequencyCheckResult:
    """判断准备发送的下一条自动回复是否触达风控频率上限（需求 13.2）。

    判定顺序为「先单会话、后单店铺」：任一维度在统计窗口内的历史回复次数达到
    （≥）其配置上限即判定暂停，并生成一条风控日志数据（风控类型
    ``frequency_limit``，需求 13.4）。两维度均未达上限或风控未启用时不暂停。

    Args:
        shop_pk: 店铺主键 shop.id（写入风控日志）。
        session_reply_times: 当前会话窗口内的历史回复时刻序列。
        shop_reply_times: 当前店铺窗口内的历史回复时刻序列。
        session_reply_limit: 单会话回复频率上限（None 表示不限制）。
        shop_reply_limit: 单店铺回复频率上限（None 表示不限制）。
        window_seconds: 统计窗口秒数（None 或 ≤0 表示不按时间窗口截断）。
        enabled: 风控规则是否启用；为 False 时不暂停（恒放行）。
        now: 参考时刻；默认取当前北京时间。可传入用于测试。

    Returns:
        风控频率检查结果 FrequencyCheckResult；达上限时 blocked=True 且携带
        risk_log 风控日志数据。
    """
    # 风控未启用：恒放行（不暂停、不记日志）
    if not enabled:
        return FrequencyCheckResult(blocked=False)

    reference = _to_beijing(now) if now is not None else now_beijing()

    # 先判单会话维度
    session_count = count_within_window(
        session_reply_times, window_seconds, now=reference
    )
    if is_limit_exceeded(session_count, session_reply_limit):
        reason = _build_reason(
            SCOPE_SESSION, session_count, session_reply_limit, window_seconds
        )
        risk_log = RiskLogData(
            shop_pk=shop_pk,
            risk_type=RISK_TYPE_FREQUENCY_LIMIT,
            trigger_reason=reason,
            log_time=reference,
        )
        return FrequencyCheckResult(
            blocked=True,
            scope=SCOPE_SESSION,
            reply_count=session_count,
            limit=session_reply_limit,
            risk_log=risk_log,
        )

    # 再判单店铺维度
    shop_count = count_within_window(
        shop_reply_times, window_seconds, now=reference
    )
    if is_limit_exceeded(shop_count, shop_reply_limit):
        reason = _build_reason(
            SCOPE_SHOP, shop_count, shop_reply_limit, window_seconds
        )
        risk_log = RiskLogData(
            shop_pk=shop_pk,
            risk_type=RISK_TYPE_FREQUENCY_LIMIT,
            trigger_reason=reason,
            log_time=reference,
        )
        return FrequencyCheckResult(
            blocked=True,
            scope=SCOPE_SHOP,
            reply_count=shop_count,
            limit=shop_reply_limit,
            risk_log=risk_log,
        )

    # 两维度均未达上限：放行
    return FrequencyCheckResult(blocked=False)


__all__ = [
    "RISK_TYPE_FREQUENCY_LIMIT",
    "SCOPE_SESSION",
    "SCOPE_SHOP",
    "RiskLogData",
    "FrequencyCheckResult",
    "count_within_window",
    "is_limit_exceeded",
    "check_reply_frequency",
]
