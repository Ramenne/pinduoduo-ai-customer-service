import asyncio
from datetime import timedelta
import pytest
from common.models.message_delivery_models import MessageInbox
from common.services import message_delivery as ledger
from common.services.greeting_guard import pure_greeting, repeated_greeting

pytestmark = pytest.mark.usefixtures('delivery_db')


def incoming(mid, text='你好', shop=1, uid='c'):
    pk = ledger.receive(shop, uid, mid, {'content': text})
    return ledger.claim(pk)


def answered(handle, state='accepted'):
    row, _ = ledger.reserve(1, 'c', f'auto:{handle[0]}:reply', 'text', '您好，请问有什么可以帮您？', handle=handle)
    ledger.settle(row['id'], {'success': True} if state == 'accepted' else None)
    ledger.finish(handle)


def test_repeat_is_suppressed_from_persistent_success_across_new_calls(delivery_db):
    first = incoming('one'); answered(first)
    second = incoming('two', '你好！')
    assert repeated_greeting(second, 1, 'c', '你好！')
    ledger.finish(second, 'repeated_greeting')
    third = incoming('three')
    assert repeated_greeting(third, 1, 'c', '你好')
    with delivery_db() as s:
        assert len(list(s.query(MessageInbox))) == 3  # 都保留，未按内容删除。


@pytest.mark.parametrize('text', ['你好，多少钱？', '您好还没发货吗', '在吗', '你好商品有货吗'])
def test_real_question_is_not_a_greeting(text):
    assert not pure_greeting(text)


def test_failure_never_counts_as_answer():
    first = incoming('one'); answered(first, 'uncertain')
    second = incoming('two')
    assert not repeated_greeting(second, 1, 'c', '你好')


def test_first_greeting_and_lost_lease_are_not_silently_handled():
    first = incoming('one')
    assert not repeated_greeting(first, 1, 'c', '你好')
    with pytest.raises(ledger.LeaseLost):
        repeated_greeting((first[0], 'old-token'), 1, 'c', '你好')


def test_new_question_breaks_greeting_sequence():
    first = incoming('one'); answered(first)
    question = incoming('q', '多少钱'); ledger.finish(question)
    second = incoming('two')
    assert not repeated_greeting(second, 1, 'c', '你好')


def test_window_and_scope_and_product_context(delivery_db):
    first = incoming('one'); answered(first)
    second = incoming('two')
    assert not repeated_greeting(second, 1, 'c', '你好', has_context=True)
    with delivery_db.begin() as s:
        row = s.get(MessageInbox, first[0]); row.created_at -= timedelta(seconds=61)
    assert not repeated_greeting(second, 1, 'c', '你好')
    other = incoming('other', uid='other')
    assert not repeated_greeting(other, 1, 'other', '你好')
    other_shop = incoming('other-shop', shop=2)
    assert not repeated_greeting(other_shop, 2, 'c', '你好')


def test_consumer_keeps_archive_but_skips_model_and_send(monkeypatch):
    from agent.agent_config import AgentConfig
    from channel_pdd.pdd_message import Context, ContextType
    from engine.message_consumer import MessageConsumer, ShopRuntime
    from engine.reply_engine import ShopConfig, ReplyRules
    first = incoming('one'); answered(first)
    runtime = ShopRuntime(ShopConfig(shop_pk=1, ai_enabled=True), ReplyRules(), AgentConfig())
    archived = []
    consumer = MessageConsumer('shop_1', 1, 9, runtime_loader=lambda _: runtime,
        conversation_control_reader=lambda *a: {'mode': 'auto', 'revision': 0}, log_writer=lambda *a: None)
    original = consumer._record_inbound_message
    def archive(*a):
        original(*a); archived.append(a)
    monkeypatch.setattr(consumer, '_record_inbound_message', archive)
    monkeypatch.setattr(consumer, '_forward_chat_event', lambda *a: None)
    async def forbidden(*a, **k): raise AssertionError('repeat must not generate or send')
    monkeypatch.setattr('engine.message_consumer.ai_reply_engine.generate_reply', forbidden)
    monkeypatch.setattr(consumer, '_send_reply', forbidden)
    outcome = asyncio.run(consumer.consume_context(Context(type=ContextType.TEXT, content='你好',
        kwargs={'from_uid': 'c', 'msg_id': 'two'})))
    assert archived and outcome.log_result == 'repeated_greeting' and not outcome.replied
