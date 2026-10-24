"""消息恢复的三个事实：收件处理权、外发效果、补拉位置。正文只在业务库保存。"""
from sqlalchemy import BigInteger, Double, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from common.models.base import AuditMixin, Base


def identity(size):
    return String(size).with_variant(String(size, collation="utf8mb4_bin"), "mysql")


class MessageInbox(AuditMixin, Base):
    __tablename__ = 'pdd_message_inbox'
    __table_args__ = (UniqueConstraint('shop_pk', 'customer_uid', 'message_id', name='uix_message_inbox'),)
    shop_pk: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    customer_uid: Mapped[str] = mapped_column(identity(128), nullable=False)
    message_id: Mapped[str] = mapped_column(identity(128), nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(String(20), nullable=False, default='pending')
    token: Mapped[str | None] = mapped_column(String(36))
    due_at: Mapped[float] = mapped_column(Double, nullable=False, default=0)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    result: Mapped[str | None] = mapped_column(String(80))
    recorded: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class MessageDelivery(AuditMixin, Base):
    __tablename__ = 'pdd_message_delivery'
    __table_args__ = (UniqueConstraint('shop_pk', 'operation_key', name='uix_message_delivery'), UniqueConstraint('shop_pk', 'request_id', name='uix_message_request'),)
    shop_pk: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    customer_uid: Mapped[str] = mapped_column(identity(128), nullable=False)
    operation_key: Mapped[str] = mapped_column(identity(128), nullable=False)
    inbox_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    body_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    baseline_ids: Mapped[str | None] = mapped_column(Text)
    baseline_at: Mapped[float | None] = mapped_column(Double)
    state: Mapped[str] = mapped_column(String(20), nullable=False)
    platform_id: Mapped[str | None] = mapped_column(String(128))
    request_id: Mapped[str] = mapped_column(identity(36), nullable=False)
    due_at: Mapped[float] = mapped_column(Double, nullable=False)
    checks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reason: Mapped[str | None] = mapped_column(String(128))
    resolved_by: Mapped[int | None] = mapped_column(BigInteger)
    resolution_note: Mapped[str | None] = mapped_column(String(500))


class MessagePollState(AuditMixin, Base):
    __tablename__ = 'pdd_message_poll_state'
    __table_args__ = (UniqueConstraint('shop_pk', name='uix_message_poll_state'),)
    shop_pk: Mapped[int] = mapped_column(BigInteger, nullable=False)
    since: Mapped[float] = mapped_column(Double, nullable=False)
    checked_at: Mapped[float] = mapped_column(Double, nullable=False)
