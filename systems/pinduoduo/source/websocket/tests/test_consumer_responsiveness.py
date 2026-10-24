"""在子进程复现消费循环空转，外层超时防止回归时卡死整个测试进程。"""
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize('mode', ['closed', 'empty', 'error'])
def test_consumer_keeps_event_loop_responsive(mode):
    script = '''
import asyncio, sys
from channel_pdd.pdd_channel import PDDChannel
from channel_pdd.message_queue import FifoMessageQueue
async def main():
    channel = PDDChannel(shop_id='offline-fixture', user_id=123)
    channel._stop_event = asyncio.Event()
    mode = sys.argv[1]
    seen = []
    async def handle(payload): seen.append(payload)
    channel._handle_message = handle
    if mode == 'closed':
        queue = FifoMessageQueue('offline-fixture')
        await queue.put('pending-message')
        queue.close()
        channel.message_queue = queue
        await channel._consume_loop()
        assert seen == ['pending-message']
    else:
        class Queue:
            closed = False
            calls = 0
            async def get(self, timeout=None):
                self.calls += 1
                if mode == 'error': raise ValueError('offline fixture')
                return None
        queue = Queue()
        channel.message_queue = queue
        task = asyncio.create_task(channel._consume_loop())
        await asyncio.sleep(.03)
        channel._stop_event.set()
        await asyncio.wait_for(task, 1)
        assert queue.calls < 20
    print('responsive')
asyncio.run(main())
'''
    root = Path(__file__).resolve().parents[2]
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([
        str(root), str(root/'websocket'), os.environ.get('PYTHONPATH', '')]))
    result = subprocess.run([sys.executable, '-c', script, mode], env=env,
                            capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr[-2000:]
    assert 'responsive' in result.stdout
