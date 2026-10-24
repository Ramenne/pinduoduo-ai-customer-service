"""连接登记不等于在线；过期会话重新登录后能重建连接。"""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import pytest
from channel_pdd import connection_registry as registry
from channel_pdd import connection_manager as manager

@pytest.fixture(autouse=True)
def clean_registry():
    registry.clear()
    yield
    registry.clear()

def channel(state):
    return SimpleNamespace(get_connection_status=lambda: {"state": state}, stop=AsyncMock(), start=AsyncMock())

@pytest.mark.parametrize("state", ["connecting", "reconnecting", "disconnected", "error", None])
def test_registered_unconnected_task_is_offline(state):
    registry.register("shop", 1, channel(state))
    assert not registry.is_connected("shop", 1)

def test_only_connected_state_is_online():
    registry.register("shop", 1, channel("connected"))
    assert registry.is_connected("shop", 1)
    assert not registry.is_connected("other", 1)

@pytest.mark.parametrize("state", ["error", "disconnected"])
def test_failed_channel_can_restart_after_login(state):
    old, new = channel(state), channel("connecting")
    registry.register("shop", 1, old)
    with patch.object(manager, "create_channel", return_value=new):
        assert asyncio.run(manager.start_channel("shop", 2, 1)) is new
    old.stop.assert_awaited_once()
    new.start.assert_awaited_once()
    assert registry.get("shop", 1) is new

@pytest.mark.parametrize("state", ["connected", "connecting", "reconnecting"])
def test_active_channel_is_not_duplicated(state):
    old = channel(state)
    registry.register("shop", 1, old)
    with patch.object(manager, "create_channel") as create:
        assert asyncio.run(manager.start_channel("shop", 2, 1)) is old
        create.assert_not_called()
    old.stop.assert_not_awaited()


def test_credential_update_replaces_connected_channel_and_closed_queue():
    old,new=channel('connected'),channel('connecting')
    registry.register('shop',1,old)
    with patch.object(manager,'create_channel',return_value=new), patch.object(manager.message_queue_manager,'remove') as remove:
        assert asyncio.run(manager.start_channel('shop',2,1,force_reconnect=True)) is new
        remove.assert_called_once_with('1:shop')
    old.stop.assert_awaited_once()
    new.start.assert_awaited_once()
    assert registry.get('shop',1) is new


def test_reconnect_installs_open_queue_that_accepts_new_message():
    from channel_pdd.message_queue import message_queue_manager
    async def scenario():
        key='7:queue-fixture'
        old_queue=message_queue_manager.get_or_create(key)
        old=channel('connected')
        async def stop_old(): await old_queue.close()
        old.stop=AsyncMock(side_effect=stop_old)
        registry.register('queue-fixture',7,old)
        def create(*_args,**_kw):
            new=channel('connecting')
            new.message_queue=message_queue_manager.get_or_create(key)
            return new
        with patch.object(manager,'create_channel',side_effect=create):
            new=await manager.start_channel('queue-fixture',2,7,force_reconnect=True)
        assert new.message_queue is not old_queue and not new.message_queue.closed
        await new.message_queue.put({'fixture':'customer-message'})
        item=await new.message_queue.get(timeout=.1)
        assert item.payload=={'fixture':'customer-message'}
        message_queue_manager.remove(key)
    asyncio.run(scenario())
