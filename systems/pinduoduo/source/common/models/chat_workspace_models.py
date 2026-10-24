"""客服工作台的同步进度与快捷短语；不保存平台凭据。"""
from datetime import datetime
from sqlalchemy import BigInteger, Boolean, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from common.models.base import AuditMixin, Base

class ChatSyncCheckpoint(AuditMixin, Base):
    __tablename__ = 'pdd_chat_sync_checkpoint'
    __table_args__ = (UniqueConstraint('shop_pk', 'customer_uid', name='uix_chat_sync_shop_customer'),)
    shop_pk: Mapped[int] = mapped_column(BigInteger, nullable=False)
    customer_uid: Mapped[str] = mapped_column(String(128), default='', server_default='', nullable=False)
    next_cursor: Mapped[str | None] = mapped_column(String(128), nullable=True)
    complete: Mapped[bool] = mapped_column(Boolean, default=False, server_default='0', nullable=False)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    recovery_json: Mapped[str | None] = mapped_column(Text, nullable=True,
        comment="连接缺口恢复游标与进度，不保存聊天正文")

class ChatQuickPhrase(AuditMixin, Base):
    __tablename__ = 'pdd_chat_quick_phrase'
    owner_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    shop_pk: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    title: Mapped[str] = mapped_column(String(64), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default='1', nullable=False)
