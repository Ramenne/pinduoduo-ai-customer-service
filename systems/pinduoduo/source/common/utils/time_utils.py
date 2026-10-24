# -*- coding: utf-8 -*-
"""
北京时间工具模块（common.utils.time_utils）
==========================================
本文件提供全链路统一的北京时间（Asia/Shanghai, UTC+8）处理能力，供
backend / websocket / scheduler 等各服务复用，满足《开发规范》第 17 条
「全链路使用北京时间」的要求。

设计要点：
- 使用 Python 3.9+ 标准库 ``zoneinfo.ZoneInfo("Asia/Shanghai")`` 实现时区，
  避免引入额外第三方依赖（如 pytz）。
- 中国大陆自 1991 年起不再实行夏令时，``Asia/Shanghai`` 相对 UTC 的偏移
  恒为固定的 +8 小时，因此「UTC → 北京时间」恒等于「UTC 时间 + 8 小时」。
- UTC 与北京时间的相互转换可逆（往返还原），对应设计文档 Property 23。

主要函数：
- now_beijing()            获取当前北京时间（带时区）
- now_beijing_naive()      获取当前北京时间（去掉时区，便于写入 DATETIME 字段）
- utc_to_beijing(dt)       UTC → 北京时间（北京时间 = UTC + 8 小时）
- beijing_to_utc(dt)       北京时间 → UTC（与 utc_to_beijing 互为可逆）
- format_beijing(dt, fmt)  将时间格式化为北京时间字符串
- parse_beijing(s, fmt)    将北京时间字符串解析为带北京时区的 datetime
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

# 北京时间时区对象（Asia/Shanghai，固定偏移 UTC+8）
BEIJING_TZ = ZoneInfo("Asia/Shanghai")

# UTC 时区对象
UTC_TZ = timezone.utc

# 默认的北京时间字符串格式（年-月-日 时:分:秒）
DEFAULT_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


def now_beijing() -> datetime:
    """获取当前北京时间（带时区信息）。

    Returns:
        当前时刻对应的北京时间 ``datetime``，其 ``tzinfo`` 为 ``BEIJING_TZ``。
    """
    return datetime.now(BEIJING_TZ)


def now_beijing_naive() -> datetime:
    """获取当前北京时间（去掉时区信息）。

    便于直接写入数据库 ``DATETIME`` 字段（库已配置 default-time-zone=+08:00）。

    Returns:
        去掉 ``tzinfo`` 的当前北京时间 ``datetime``。
    """
    return now_beijing().replace(tzinfo=None)


def utc_to_beijing(dt: datetime) -> datetime:
    """将 UTC 时间转换为北京时间（北京时间 = UTC + 8 小时）。

    入参既可为带时区的 ``datetime``，也可为不带时区的「朴素」UTC 时间：
    - 若入参不带时区（naive），则视为 UTC 时间；
    - 若入参带时区，则按其真实时区先归一到 UTC，再转换为北京时间。

    Args:
        dt: 待转换的 UTC 时间 ``datetime``。

    Returns:
        转换后的北京时间 ``datetime``（带 ``BEIJING_TZ`` 时区）。
    """
    # 朴素时间一律按 UTC 解释，补齐 UTC 时区
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC_TZ)
    # astimezone 会按固定 +8 偏移换算为北京时间
    return dt.astimezone(BEIJING_TZ)


def beijing_to_utc(dt: datetime) -> datetime:
    """将北京时间转换为 UTC 时间（与 ``utc_to_beijing`` 互为可逆）。

    入参既可为带时区的 ``datetime``，也可为不带时区的「朴素」北京时间：
    - 若入参不带时区（naive），则视为北京时间；
    - 若入参带时区，则按其真实时区归一到 UTC。

    Args:
        dt: 待转换的北京时间 ``datetime``。

    Returns:
        转换后的 UTC 时间 ``datetime``（带 ``UTC_TZ`` 时区）。
    """
    # 朴素时间一律按北京时间解释，补齐北京时区
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=BEIJING_TZ)
    # astimezone 会按固定 -8 偏移换算回 UTC
    return dt.astimezone(UTC_TZ)


def format_beijing(dt: datetime, fmt: str = DEFAULT_TIME_FORMAT) -> str:
    """将 ``datetime`` 格式化为北京时间字符串。

    - 若入参不带时区（naive），则视为北京时间，直接按格式输出；
    - 若入参带时区，则先换算到北京时间，再按格式输出。

    Args:
        dt: 待格式化的时间 ``datetime``。
        fmt: 时间格式，默认 ``%Y-%m-%d %H:%M:%S``。

    Returns:
        北京时间字符串。
    """
    # 带时区的时间先统一换算到北京时间，保证输出为北京时间口径
    if dt.tzinfo is not None:
        dt = dt.astimezone(BEIJING_TZ)
    return dt.strftime(fmt)


def parse_beijing(value: str, fmt: str = DEFAULT_TIME_FORMAT) -> datetime:
    """将北京时间字符串解析为带北京时区的 ``datetime``。

    Args:
        value: 北京时间字符串。
        fmt: 字符串对应的时间格式，默认 ``%Y-%m-%d %H:%M:%S``。

    Returns:
        解析得到并附加 ``BEIJING_TZ`` 时区的 ``datetime``。
    """
    # 解析得到的是朴素时间，按北京时间口径补齐时区
    return datetime.strptime(value, fmt).replace(tzinfo=BEIJING_TZ)


def safe_isoformat(value: Optional[datetime]) -> Optional[str]:
    """安全地将 ``datetime`` 序列化为 ISO 8601 字符串。

    仅在 ``value`` 为真值时调用 ``isoformat()``，否则返回 ``None``；
    本函数不做时区转换，调用方传入什么时区就输出什么时区。

    Args:
        value: 待序列化的 ``datetime``，允许为 ``None``。

    Returns:
        ``value.isoformat()`` 字符串；当 ``value`` 为 ``None`` 时返回 ``None``。
    """
    return value.isoformat() if value else None
