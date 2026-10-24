import asyncio
import json
import pytest
from sqlalchemy import select
from channel_pdd.live_message_poll import LiveMessagePoll
from channel_pdd import gap_recovery as gap
from common.services import message_delivery as ledger
from common.models.chat_workspace_models import ChatSyncCheckpoint
from common.models.log_models import ChatMessage
from common.models.message_delivery_models import MessageInbox

pytestmark=pytest.mark.usefixtures('delivery_db')


def raw(mid,ts=1800,role='user'):
    return {'msg_id':mid,'ts':ts,'from':{'role':role,'uid':'c' if role=='user' else 'cs'},
        'to':{'role':'mall_cs' if role=='user' else 'user','uid':'cs' if role=='user' else 'c'},'type':0,'content':'fixture'}


def setup(monkeypatch):
    now=[3000]
    monkeypatch.setattr(gap.time,'time',lambda:now[0])
    async def no_push(*a):pass
    monkeypatch.setattr(gap,'forward_new_message',no_push)
    poll=LiveMessagePoll('shop_1',1,9,None,clock=lambda:2000)
    ledger.poll_position(1,1000)
    def step(active=True):
        now[0]+=400
        asyncio.run(poll.gap.step(active))
    return poll,step


def test_more_than_four_conversations_and_three_history_pages_resume(monkeypatch,delivery_db):
    poll,tick=setup(monkeypatch);poll.gap.request();calls=[]
    monkeypatch.setattr(poll.gap,'fetch_list',lambda page:{'success':True,'conversations':[{'customer_uid':str(n),'last_ts':1800} for n in range(6)],'has_more':False})
    def fetch(uid,cursor):
        page=int(cursor or 0);calls.append((uid,page))
        row=raw(str(page+1),1800-page*10,'mall_cs');row['to']['uid']=uid
        return {'success':True,'messages':[row],'has_more':page<4}
    monkeypatch.setattr(poll.gap,'fetch_page',fetch)
    tick()
    for _ in range(3):tick()
    # 新实例从数据库同一游标继续，不重头读取已保存页面。
    replacement=LiveMessagePoll('shop_1',1,9,None,clock=lambda:2000)
    replacement.gap.fetch_list=poll.gap.fetch_list;replacement.gap.fetch_page=fetch
    poll.gap=replacement.gap
    for _ in range(28):tick()
    with delivery_db() as s:
        job=json.loads(s.scalar(select(ChatSyncCheckpoint)).recovery_json)
        assert job['done'] and job['pages']==31
        assert len(list(s.scalars(select(ChatMessage))))==30
    assert len(calls)==30 and len(set(calls))==30
    tick();assert len(calls)==30


def test_failure_keeps_cursor_and_retries_later_without_losing_history(monkeypatch,delivery_db):
    poll,tick=setup(monkeypatch);poll.gap.request()
    monkeypatch.setattr(poll.gap,'fetch_list',lambda page:{'success':True,'conversations':[{'customer_uid':'c','last_ts':1800}]})
    tick()
    monkeypatch.setattr(poll.gap,'fetch_page',lambda *a:{'success':False})
    tick()
    with delivery_db() as s:
        job=json.loads(s.scalar(select(ChatSyncCheckpoint)).recovery_json)
        assert job['pending'][0]['cursor'] is None and job['retries']==1
    monkeypatch.setattr(poll.gap,'fetch_page',lambda *a:{'success':True,'messages':[raw('m')],'has_more':False})
    tick()
    with delivery_db() as s:
        assert json.loads(s.scalar(select(ChatSyncCheckpoint)).recovery_json)['done']
        assert s.scalar(select(MessageInbox)).state=='pending'


def test_do_not_reply_until_all_pages_and_newest_unanswered_only(monkeypatch,delivery_db):
    poll,tick=setup(monkeypatch);poll.gap.request()
    monkeypatch.setattr(poll.gap,'fetch_list',lambda page:{'success':True,'conversations':[{'customer_uid':'c','last_ts':1800}]})
    tick()
    monkeypatch.setattr(poll.gap,'fetch_page',lambda uid,cursor:({'success':True,'messages':[raw('new',1800)],'has_more':True} if cursor is None else {'success':True,'messages':[raw('old',1700)],'has_more':False}))
    tick()
    with delivery_db() as s:assert s.scalar(select(MessageInbox)).state=='backfill'
    tick()
    with delivery_db() as s:
        rows={r.message_id:r.state for r in s.scalars(select(MessageInbox))}
        assert rows=={'new':'pending','old':'done'}


def test_ai_disabled_archives_without_releasing_reply(monkeypatch,delivery_db):
    poll,tick=setup(monkeypatch);poll.gap.request()
    monkeypatch.setattr(poll.gap,'fetch_list',lambda page:{'success':True,'conversations':[{'customer_uid':'c','last_ts':1800}]})
    monkeypatch.setattr(poll.gap,'fetch_page',lambda *a:{'success':True,'messages':[raw('m')]})
    tick(False);tick(False)
    with delivery_db() as s:
        assert s.scalar(select(MessageInbox)).state=='done'
        assert s.scalar(select(ChatMessage)).msg_id=='m'


def test_outbound_in_newer_page_prevents_reply_to_older_question(monkeypatch,delivery_db):
    poll,tick=setup(monkeypatch);poll.gap.request()
    monkeypatch.setattr(poll.gap,'fetch_list',lambda page:{'success':True,'conversations':[{'customer_uid':'c','last_ts':1800}]})
    monkeypatch.setattr(poll.gap,'fetch_page',lambda uid,cursor:({'success':True,'messages':[raw('reply',1800,'mall_cs')],'has_more':True} if cursor is None else {'success':True,'messages':[raw('question',1700)],'has_more':False}))
    tick();tick();tick()
    with delivery_db() as s:assert s.scalar(select(MessageInbox)).state=='done'


def test_wrong_customer_pauses_without_cross_customer_archive(monkeypatch,delivery_db):
    poll,tick=setup(monkeypatch);poll.gap.request()
    monkeypatch.setattr(poll.gap,'fetch_list',lambda page:{'success':True,'conversations':[{'customer_uid':'different','last_ts':1800}]})
    monkeypatch.setattr(poll.gap,'fetch_page',lambda *a:{'success':True,'messages':[raw('m')]})
    tick();tick()
    with delivery_db() as s:
        assert json.loads(s.scalar(select(ChatSyncCheckpoint)).recovery_json)['paused']
        assert s.scalar(select(ChatMessage)) is None
