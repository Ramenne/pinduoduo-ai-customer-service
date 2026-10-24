"""现有店铺循环的恢复协调：收件接续、只读外发核对、按持久位置补拉。"""
import asyncio
import logging
import time
from datetime import timezone, timedelta
from sqlalchemy import select
from channel_pdd.api.get_chat_history import GetChatHistory
from channel_pdd.pdd_message import Context, ContextType
from channel_pdd.reliable_delivery import reconcile
from channel_pdd.chat_event_forwarder import forward_control_state
from common.services.conversation_control import read_committed
from common.db.session import session_scope
from common.models.config_models import LlmConfig
from common.models.shop_models import Shop, Account
from common.services import message_delivery as ledger

logger = logging.getLogger(__name__)


def timestamp(raw):
    try:
        value = float(raw or 0)
        return value / 1000 if value > 100000000000 else value
    except (TypeError, ValueError):
        return 0


def enabled(shop_pk):
    with session_scope() as session:
        shop = session.get(Shop, shop_pk)
        if shop is None or shop.status != 1 or shop.merged_into_shop_pk:
            return False, 0
        config = session.scalar(select(LlmConfig).where(LlmConfig.shop_pk == shop_pk))
        if config is None:
            config = session.scalar(select(LlmConfig).where(LlmConfig.shop_pk.is_(None)))
        since = 0
        if config and config.updated_at:
            date = config.updated_at
            since = date.replace(tzinfo=timezone(timedelta(hours=8))).timestamp() if date.tzinfo is None else date.timestamp()
        return bool(config and config.ai_enabled and config.api_key_enc), since


def authorized(shop_pk, user_id):
    with session_scope() as session:
        shop=session.get(Shop,shop_pk)
        if not shop or shop.owner_user_id != user_id or shop.status != 1 or shop.merged_into_shop_pk:
            return False
        account=session.scalar(select(Account).where(Account.shop_pk==shop_pk,Account.user_id==user_id))
        return bool(account and account.login_state != 'relogin')


class LiveMessagePoll:
    def __init__(self, shop_id, shop_pk, user_id, enqueue, *, interval=60, clock=time.time):
        self.shop_id, self.shop_pk, self.user_id = shop_id, shop_pk, user_id
        self.enqueue, self.interval, self.clock = enqueue, interval, clock
        self.armed_at = clock()
        self.failures = 0
        self.latest = {}
        self._wake = asyncio.Event()
        self._pull_requested = False
        self._last_pull = float('-inf')
        from channel_pdd.gap_recovery import GapRecovery
        self.gap = GapRecovery(self)

    def connected(self):
        """只在连接建立/重建事件请求一次缺口补偿，短时间重连合并。"""
        self._pull_requested = True
        self._wake.set()

    def take_pull(self):
        if not self._pull_requested or time.monotonic() - self._last_pull < 60:
            return False
        self._pull_requested = False
        self._last_pull = time.monotonic()
        return True

    def archive(self, uid, rows):
        # 纯归档不请求平台；复用手动同步历史的解析、消重和事务。
        fetcher = GetChatHistory.__new__(GetChatHistory)
        fetcher.user_id = self.user_id
        saved = []
        parsed = [fetcher._parse_message(r) for r in rows if r.get('msg_id')]
        parsed.sort(key=lambda r: r.get('ts') or 0)
        fetcher._persist_history(self.shop_pk, str(uid), parsed, saved=saved)
        return saved

    async def recover(self):
        affected = await asyncio.to_thread(reconcile, self.shop_pk, self.shop_id, self.user_id)
        for uid in affected or []:
            state=await asyncio.to_thread(read_committed,self.shop_pk,uid)
            await forward_control_state(self.shop_pk,uid,state)
        for _, payload in await asyncio.to_thread(ledger.recoverable, self.shop_pk):
            await self.enqueue(Context(type=ContextType(payload['type']), content=payload['content'],
                goods_context=payload.get('goods_context'), order_context=payload.get('order_context'),
                kwargs=payload['kwargs']))

    async def check(self, *, pull=True):
        if not await asyncio.to_thread(authorized,self.shop_pk,self.user_id):return
        await self.recover()
        active,_ = await asyncio.to_thread(enabled,self.shop_pk)
        if pull:await asyncio.to_thread(self.gap.request)
        await self.gap.step(active)

    async def run(self):
        while True:
            try:
                pull = self.take_pull()
                self._wake.clear()
                await self.check(pull=pull)
            except asyncio.CancelledError:
                raise
            except Exception:
                self.failures += 1
                logger.exception('店铺消息恢复未完成: shop_pk=%s', self.shop_pk)
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=10)
            except asyncio.TimeoutError:
                pass  # 仅本地收件/外发账恢复；不触发HTTP补拉。
