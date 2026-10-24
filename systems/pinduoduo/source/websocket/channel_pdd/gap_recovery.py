"""原同步进度上的连接缺口任务：每批一页、游标持久化、只在有欠账时查询。"""
import asyncio
import json
import time
from sqlalchemy import select
from common.db.session import session_scope
from common.models.shop_models import Shop
from common.models.chat_workspace_models import ChatSyncCheckpoint
from common.models.message_delivery_models import MessageInbox
from common.models.log_models import ChatMessage
from common.services import message_delivery as ledger
from common.utils.time_utils import now_beijing_naive
from channel_pdd.api.get_conversations import GetConversations
from channel_pdd.api.get_chat_history import GetChatHistory
from channel_pdd.pdd_message import PDDChatMessage, ContextType
from channel_pdd.chat_event_forwarder import forward_new_message


def stamp(value):
    try:
        n=float(value or 0)
        return n/1000 if n>100000000000 else n
    except (ValueError,TypeError):return 0


class GapRecovery:
    def __init__(self, poll):self.poll=poll

    def row(self,s):
        p=self.poll
        shop=s.scalar(select(Shop).where(Shop.id==p.shop_pk).with_for_update())
        if not shop or shop.owner_user_id!=p.user_id or shop.status!=1 or shop.merged_into_shop_pk:
            raise ValueError('店铺归属或启用状态已改变')
        row=s.scalar(select(ChatSyncCheckpoint).where(ChatSyncCheckpoint.shop_pk==p.shop_pk,
            ChatSyncCheckpoint.customer_uid=='').with_for_update())
        if row is None:
            row=ChatSyncCheckpoint(shop_pk=p.shop_pk,customer_uid='');s.add(row);s.flush()
        return row

    def request(self):
        p=self.poll
        since=ledger.poll_position(p.shop_pk,p.clock())
        with session_scope() as s:
            row=self.row(s);job=json.loads(row.recovery_json or 'null')
            if not job or job.get('done') or job.get('owner')!=p.user_id:
                job={'owner':p.user_id,'since':since,'frontier':since,'page':1,
                    'pending':[],'listed':False,'due':0,'retries':0,'done':False,'pages':0,'list_seen':[]}
            else:
                job['due']=0;job['paused']=False
            row.recovery_json=json.dumps(job);row.complete=False
            row.last_error=None

    def load(self):
        with session_scope() as s:
            row=self.row(s);raw=row.recovery_json
            return raw,json.loads(raw or 'null')

    def save(self, raw, job, error=None):
        with session_scope() as s:
            row=self.row(s)
            if row.recovery_json!=raw:raise ValueError('恢复任务已由其他执行器推进')
            row.recovery_json=json.dumps(job);row.last_attempt_at=now_beijing_naive()
            row.complete=bool(job.get('done'));row.last_error=error
            if row.complete:row.last_success_at=row.last_attempt_at

    def fetch_list(self, page):
        return GetConversations(self.poll.shop_id,self.poll.user_id).fetch(page=page,size=100,recovery=True)

    def fetch_page(self, uid, cursor):
        return GetChatHistory(self.poll.shop_id,self.poll.user_id).fetch_page(uid,start_msg_id=cursor,size=50,recovery=True)

    def stage(self, rows):
        p=self.poll
        for raw in rows:
            context=PDDChatMessage({'response':'push','message':raw}).to_context()
            if context.kwargs.get('from_user')=='user' and context.type in (
                    ContextType.TEXT,ContextType.GOODS_INQUIRY,ContextType.GOODS_SPEC,ContextType.ORDER_INFO,ContextType.IMAGE,ContextType.VIDEO):
                from engine.message_consumer import MessageConsumer
                ledger.receive(p.shop_pk,context.kwargs.get('from_uid'),context.kwargs.get('msg_id'),
                    MessageConsumer.context_payload(context),defer=True)

    def release(self, uid, since, last_out, active):
        p=self.poll
        with session_scope() as s:
            self.row(s)
            held=list(s.scalars(select(MessageInbox).where(MessageInbox.shop_pk==p.shop_pk,
                MessageInbox.customer_uid==str(uid),MessageInbox.state=='backfill').with_for_update()))
            candidates=[(r,stamp(json.loads(r.payload).get('kwargs',{}).get('timestamp'))) for r in held]
            latest=max(candidates,key=lambda pair:(pair[1],pair[0].id),default=(None,0))
            newest=s.scalar(select(ChatMessage).where(ChatMessage.shop_pk==p.shop_pk,
                ChatMessage.customer_uid==str(uid),ChatMessage.direction=='in').order_by(ChatMessage.msg_time.desc(),ChatMessage.id.desc()).limit(1))
            for row,ts in candidates:
                resume=(active and row is latest[0] and ts>=since and ts>last_out and
                    newest is not None and newest.msg_id==row.message_id)
                row.state='pending' if resume else 'done'
                row.token=None;row.due_at=0
                row.result=None if resume else 'recovered_history_only'

    async def step(self, active):
        raw,job=await asyncio.to_thread(self.load)
        if not job or job.get('done') or job.get('paused') or job['due']>time.time():return
        try:
            if not job['pending'] and not job['listed']:
                page=await asyncio.to_thread(self.fetch_list,job['page'])
                if not page.get('success'):raise RuntimeError('auth' if page.get('auth_required') else 'temporary')
                rows=page.get('conversations',[])
                if page.get('has_more') and not rows:raise RuntimeError('invalid_page')
                import hashlib
                fingerprint=hashlib.sha256(json.dumps([c.get('customer_uid') for c in rows]).encode()).hexdigest()
                if rows and fingerprint in job['list_seen']:raise RuntimeError('invalid_page')
                job['list_seen']=(job['list_seen']+[fingerprint])[-32:]
                for conv in rows:
                    uid=conv.get('customer_uid')
                    if not uid:raise RuntimeError('invalid_identity')
                    if stamp(conv.get('last_ts'))>=job['since']:
                        job['pending'].append({'uid':str(uid),'cursor':None,'seen':[],'last_out':0})
                job['listed']=not page.get('has_more',False);job['page']+=1
            elif job['pending']:
                task=job['pending'][0];uid=task['uid']
                page=await asyncio.to_thread(self.fetch_page,uid,task['cursor'])
                if not page.get('success'):raise RuntimeError('auth' if page.get('auth_required') else 'temporary')
                rows=page.get('messages',[])
                if any(not r.get('msg_id') or not 0<stamp(r.get('ts'))<=self.poll.clock() for r in rows):
                    raise RuntimeError('invalid_message')
                for item in rows:
                    sender=item.get('from') or {};target=item.get('to') or {}
                    if ((sender.get('role')=='user' and str(sender.get('uid'))!=uid) or
                        (sender.get('role')=='mall_cs' and str(target.get('uid'))!=uid)):
                        raise RuntimeError('invalid_identity')
                await asyncio.to_thread(self.stage,rows)
                saved=await asyncio.to_thread(self.poll.archive,uid,rows)
                for item in saved:await forward_new_message(self.poll.shop_pk,uid,{**item,'archive_sync':True})
                clocks=[stamp(r.get('ts')) for r in rows]
                job['frontier']=max([job['frontier']]+clocks)
                task['last_out']=max([task['last_out']]+[stamp(r.get('ts')) for r in rows if (r.get('from') or {}).get('role')=='mall_cs'])
                if not page.get('has_more') or (clocks and min(clocks)<=job['since']):
                    await asyncio.to_thread(self.release,uid,job['since'],task['last_out'],active)
                    job['pending'].pop(0)
                else:
                    cursor=str(rows[-1]['msg_id']) if rows else None
                    if not cursor or cursor in task['seen']:raise RuntimeError('invalid_cursor')
                    task['cursor']=cursor;task['seen']=(task['seen']+[cursor])[-32:]
            job['pages']+=1;job['retries']=0;job['due']=time.time()+10
            if job['listed'] and not job['pending']:job['done']=True
            await asyncio.to_thread(self.save,raw,job)
            if job['done']:
                await asyncio.to_thread(ledger.advance_poll,self.poll.shop_pk,max(job['since'],job['frontier']-2))
        except Exception as exc:
            from sqlalchemy.exc import SQLAlchemyError
            job['retries']+=1;job['due']=time.time()+min(300,15*2**min(job['retries'],5))
            job['paused']=(str(exc)=='auth' or str(exc).startswith('invalid') or
                not isinstance(exc,(RuntimeError,SQLAlchemyError)))
            await asyncio.to_thread(self.save,raw,job,'恢复暂未完成，已保存进度；授权/分页异常需处理后继续' if job['paused'] else '恢复暂时受阻，已保存进度，稍后继续')
