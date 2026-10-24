# -*- coding: utf-8 -*-
"""
common.db.retry —— 数据库连接失败重试工具
==========================================
本文件用途：为数据库操作提供「连接失败自动重试」能力（开发规范 13：数据库
操作必须考虑连接失败重试）。基于 SQLAlchemy 2.0（同步）实现，提供：
- is_db_disconnect_error()：判断异常是否为「连接断开类」错误（值得重试）；
- with_db_retry()：同步函数重试装饰器，采用指数退避；
- retry_call()：对任意可调用对象执行带重试的调用（不便加装饰器时使用）。

设计原则：
- 仅对「连接断开 / 无法连接」类错误重试；业务错误（完整性约束 IntegrityError、
  SQL 语法错误 ProgrammingError 等）立即抛出，避免无效重试放大问题。
- 指数退避：每次失败后等待时间按 backoff_factor 放大，封顶 max_delay。
- 配合 session.py 的 pool_pre_ping=True，重试时连接池会自动剔除失效连接。

注意：所有 SQL 仍须使用参数化查询（规范 16），本模块不涉及 SQL 拼接。
"""
from __future__ import annotations

import functools
import logging
import time
from typing import Any, Callable, TypeVar

from sqlalchemy.exc import (
    DBAPIError,
    DisconnectionError,
    InterfaceError,
    OperationalError,
)

# 模块级日志记录器（禁用 debug 级别，统一使用 warning/error —— 规范 38）
logger = logging.getLogger(__name__)

T = TypeVar("T")

# 连接断开类错误的关键字。不同驱动（PyMySQL）与 SQLAlchemy 包装下错误文案不
# 一致，故以字符串兜底匹配，覆盖常见网络抖动与服务端断连场景。
_RETRYABLE_KEYWORDS: tuple[str, ...] = (
    "Lost connection",                    # MySQL 2013 - 查询过程中连接丢失
    "MySQL server has gone away",         # MySQL 2006 - 服务端关闭空闲连接
    "Server has gone away",               # 同上变体
    "Can't connect to MySQL",             # MySQL 2003 - 无法建立连接
    "Connection refused",                 # 服务未启动 / 端口不通
    "Connection reset",                   # TCP RST
    "Broken pipe",                        # 写入已关闭的连接
    "BrokenPipeError",                    # 同上 Python 异常名
    "Connection was killed",              # 服务端 KILL CONNECTION
    "Connection timed out",               # 连接超时
    "WinError 64",                        # 指定的网络名不再可用（Windows）
    "WinError 10054",                     # 远程主机强迫关闭了一个现有的连接
    "WinError 10053",                     # 本机软件中止了一个已建立的连接
    "WinError 10060",                     # 连接尝试超时失败
)


def is_db_disconnect_error(exc: BaseException) -> bool:
    """判断异常是否为数据库连接断开类错误（值得重试）。

    判定顺序：
    1. SQLAlchemy 明确的 DisconnectionError / InterfaceError → 直接可重试；
    2. OperationalError / DBAPIError → 检查错误文案是否命中关键字；
    3. 其他类型 → 兜底字符串匹配（驱动可能抛出未被 SQLAlchemy 包装的异常）。

    Args:
        exc: 待判定的异常实例

    Returns:
        True 表示连接断开类错误（可重试）；False 表示业务错误（不应重试）。
    """
    # 明确的连接错误类，无需检查文案
    if isinstance(exc, (DisconnectionError, InterfaceError)):
        return True

    # SQLAlchemy 包装的数据库异常，检查文案关键字
    if isinstance(exc, (OperationalError, DBAPIError)):
        msg = str(exc)
        return any(keyword in msg for keyword in _RETRYABLE_KEYWORDS)

    # 兜底：未被 SQLAlchemy 包装的原生异常
    msg = str(exc)
    return any(keyword in msg for keyword in _RETRYABLE_KEYWORDS)


def retry_call(
    func: Callable[..., T],
    *args: Any,
    max_retries: int = 3,
    initial_delay: float = 1.0,
    max_delay: float = 8.0,
    backoff_factor: float = 2.0,
    **kwargs: Any,
) -> T:
    """对可调用对象执行带「连接失败重试」的调用（指数退避）。

    仅对连接断开类错误重试；其余异常立即抛出。总执行次数 = 1 + max_retries。

    Args:
        func: 待执行的可调用对象（同步函数）
        *args: 透传给 func 的位置参数
        max_retries: 最大重试次数（不含首次执行）
        initial_delay: 首次重试前等待秒数
        max_delay: 单次等待时间上限
        backoff_factor: 每次失败后等待时间的放大倍数
        **kwargs: 透传给 func 的关键字参数

    Returns:
        func 的返回值

    Raises:
        最后一次失败的异常（连接错误重试耗尽，或非连接类业务错误）。
    """
    delay = initial_delay
    last_exc: BaseException | None = None

    # 总执行次数 = 首次 1 次 + max_retries 次重试
    for attempt in range(max_retries + 1):
        try:
            return func(*args, **kwargs)
        except BaseException as exc:
            last_exc = exc

            # 业务错误立即抛出，不重试
            if not is_db_disconnect_error(exc):
                raise

            # 已达重试上限，放弃并抛出
            if attempt >= max_retries:
                logger.error(
                    "数据库操作 %s 已重试 %d 次仍失败，放弃执行: %s",
                    getattr(func, "__qualname__", repr(func)),
                    max_retries,
                    exc,
                )
                raise

            # 记录重试并按指数退避等待
            logger.warning(
                "数据库操作 %s 第 %d 次执行失败（连接断开类错误），%.1f 秒后重试: %s",
                getattr(func, "__qualname__", repr(func)),
                attempt + 1,
                delay,
                exc,
            )
            time.sleep(delay)
            delay = min(delay * backoff_factor, max_delay)

    # 理论上不可达（要么 return 要么 raise）
    assert last_exc is not None, "重试循环异常退出但 last_exc 为空"
    raise last_exc


def with_db_retry(
    max_retries: int = 3,
    initial_delay: float = 1.0,
    max_delay: float = 8.0,
    backoff_factor: float = 2.0,
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """同步数据库操作「连接失败重试」装饰器（指数退避）。

    仅对连接断开类错误重试，业务错误立即抛出。

    重试间隔（指数退避）：
        第 1 次失败后等待 initial_delay 秒；
        第 2 次失败后等待 initial_delay * backoff_factor 秒；
        ……单次等待不超过 max_delay 秒。

    Args:
        max_retries: 最大重试次数（不含首次执行），默认 3（最多执行 4 次）
        initial_delay: 首次重试前等待秒数（默认 1.0）
        max_delay: 单次等待时间上限（默认 8.0）
        backoff_factor: 每次失败后等待时间放大倍数（默认 2.0）

    Returns:
        装饰器函数。

    Example:
        >>> @with_db_retry(max_retries=3, initial_delay=1.0)
        ... def write_record(payload):
        ...     with session_scope() as session:
        ...         session.execute(text("INSERT ... VALUES (:v)"), {"v": payload})
    """

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            return retry_call(
                func,
                *args,
                max_retries=max_retries,
                initial_delay=initial_delay,
                max_delay=max_delay,
                backoff_factor=backoff_factor,
                **kwargs,
            )

        return wrapper

    return decorator


__all__ = [
    "is_db_disconnect_error",
    "with_db_retry",
    "retry_call",
]
