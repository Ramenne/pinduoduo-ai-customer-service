from __future__ import annotations

import asyncio
import threading

import httpx
import pytest

from oin_control_plane.runtime_io import RuntimeIoBusy, RuntimeIoLane, RuntimeIoTimeout


def test_blocking_runtime_io_does_not_block_the_event_loop() -> None:
    async def scenario() -> None:
        release = threading.Event()
        lane = RuntimeIoLane(max_workers=1, max_in_flight=1)
        call = asyncio.create_task(lane.call(release.wait))
        await asyncio.sleep(0.02)
        assert not call.done()
        release.set()
        assert await call is True
        lane.close()

    asyncio.run(scenario())


def test_timeout_keeps_capacity_reserved_until_worker_really_finishes() -> None:
    async def scenario() -> None:
        release = threading.Event()
        lane = RuntimeIoLane(max_workers=1, max_in_flight=1, default_timeout_seconds=0.02)
        with pytest.raises(RuntimeIoTimeout, match="runtime_io_timeout"):
            await lane.call(release.wait)
        assert lane.in_flight == 1
        with pytest.raises(RuntimeIoBusy, match="runtime_io_saturated"):
            await lane.call(lambda: True)
        release.set()
        for _ in range(50):
            if lane.in_flight == 0:
                break
            await asyncio.sleep(0.01)
        assert lane.in_flight == 0
        assert await lane.call(lambda: "recovered") == "recovered"
        lane.close()

    asyncio.run(scenario())


def test_runtime_io_propagates_dependency_errors() -> None:
    async def scenario() -> None:
        lane = RuntimeIoLane(max_workers=1, max_in_flight=1)

        def fail() -> None:
            raise ValueError("database rejected operation")

        with pytest.raises(ValueError, match="database rejected operation"):
            await lane.call(fail)
        lane.close()

    asyncio.run(scenario())


def test_blocked_database_health_probe_does_not_freeze_download_route(
    app, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = threading.Event()
    release = threading.Event()

    def blocked_ping() -> bool:
        started.set()
        release.wait(timeout=5)
        return True

    monkeypatch.setattr(app.state.database, "ping", blocked_ping)

    async def scenario() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="https://testserver"
        ) as client:
            health_call = asyncio.create_task(client.get("/healthz"))
            assert await asyncio.to_thread(started.wait, 1)
            download = await asyncio.wait_for(client.get("/download/apk"), timeout=1)
            assert download.status_code == 200
            health = await asyncio.wait_for(health_call, timeout=3)
            assert health.status_code == 200
            assert health.json()["database"] == "error"
        release.set()

    try:
        asyncio.run(scenario())
    finally:
        release.set()
        app.state.runtime_io.close()
