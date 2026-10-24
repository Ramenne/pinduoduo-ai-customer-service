from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from threading import Lock
from typing import Any, Callable, TypeVar


T = TypeVar("T")


class RuntimeIoBusy(RuntimeError):
    pass


class RuntimeIoTimeout(RuntimeError):
    pass


class RuntimeIoLane:
    """Bounded lane for synchronous runtime I/O called from the ASGI event loop.

    Timed-out work remains counted until the underlying thread really exits. This
    prevents an unavailable dependency from creating an unbounded thread queue.
    """

    def __init__(
        self,
        *,
        max_workers: int = 4,
        max_in_flight: int = 8,
        default_timeout_seconds: float | None = None,
    ) -> None:
        if max_workers < 1 or max_in_flight < max_workers:
            raise ValueError("invalid runtime I/O lane capacity")
        if default_timeout_seconds is not None and default_timeout_seconds <= 0:
            raise ValueError("runtime I/O timeout must be positive")
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="oin-runtime-io",
        )
        self._max_in_flight = max_in_flight
        self._default_timeout_seconds = default_timeout_seconds
        self._in_flight = 0
        self._lock = Lock()
        self._closed = False

    async def call(
        self,
        function: Callable[..., T],
        /,
        *args: Any,
        timeout_seconds: float | None = None,
        **kwargs: Any,
    ) -> T:
        with self._lock:
            if self._closed:
                raise RuntimeIoBusy("runtime_io_closed")
            if self._in_flight >= self._max_in_flight:
                raise RuntimeIoBusy("runtime_io_saturated")
            self._in_flight += 1

        loop = asyncio.get_running_loop()
        try:
            concurrent = self._executor.submit(partial(function, *args, **kwargs))
        except BaseException:
            self._release()
            raise
        concurrent.add_done_callback(lambda _future: self._release())
        wrapped = asyncio.wrap_future(concurrent, loop=loop)
        timeout = (
            self._default_timeout_seconds
            if timeout_seconds is None
            else timeout_seconds
        )
        if timeout is None:
            # Do not abandon writes while their worker can still commit. Database
            # connect/statement/lock timeouts bound the dependency itself; explicit
            # caller deadlines are reserved for read-only probes such as healthz.
            return await wrapped
        try:
            return await asyncio.wait_for(asyncio.shield(wrapped), timeout=timeout)
        except TimeoutError as exc:
            raise RuntimeIoTimeout("runtime_io_timeout") from exc

    def close(self) -> None:
        with self._lock:
            self._closed = True
        self._executor.shutdown(wait=False, cancel_futures=True)

    @property
    def in_flight(self) -> int:
        with self._lock:
            return self._in_flight

    def _release(self) -> None:
        with self._lock:
            self._in_flight -= 1
