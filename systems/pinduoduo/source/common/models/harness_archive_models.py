"""原会话的冷归档/摘要与内部运行经验；不拥有收发或正式经营知识权。"""
from sqlalchemy import BigInteger, Integer, Double, String, Text, UniqueConstraint, Index
from sqlalchemy.dialects.mysql import MEDIUMTEXT
from sqlalchemy.orm import Mapped, mapped_column
from common.models.base import AuditMixin, Base
from common.models.message_delivery_models import identity

LargeText = Text().with_variant(MEDIUMTEXT(), 'mysql')


class ContextArchive(AuditMixin, Base):
    __tablename__ = 'pdd_context_archive'
    __table_args__ = (
        UniqueConstraint('shop_pk','customer_uid','kind','stable_id','source_hash', name='uix_context_snapshot'),
        Index('ix_context_scope','shop_pk','customer_uid','kind','id'),)
    shop_pk: Mapped[int] = mapped_column(BigInteger, nullable=False)
    owner_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    customer_uid: Mapped[str] = mapped_column(identity(128), nullable=False)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    stable_id: Mapped[str] = mapped_column(String(96), nullable=False)
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[str] = mapped_column(LargeText, nullable=False)
    retrieval_text: Mapped[str] = mapped_column(Text, nullable=False, default='')
    summary_json: Mapped[str | None] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String(24), nullable=False, default='stored')
    token: Mapped[str | None] = mapped_column(String(36))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    due_at: Mapped[float] = mapped_column(Double, nullable=False, default=0, index=True)
    error: Mapped[str | None] = mapped_column(String(128))
    inbox_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    delivery_key: Mapped[str | None] = mapped_column(String(128))


class HarnessExperience(AuditMixin, Base):
    __tablename__ = 'pdd_harness_experience'
    __table_args__ = (UniqueConstraint('shop_pk','source_key', name='uix_harness_observation'),)
    shop_pk: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    owner_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    customer_uid: Mapped[str] = mapped_column(identity(128), nullable=False, default='')
    source_key: Mapped[str] = mapped_column(identity(128), nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    observation: Mapped[str] = mapped_column(Text, nullable=False)
    guidance: Mapped[str] = mapped_column(Text, nullable=False, default='')
    trigger_terms: Mapped[str] = mapped_column(String(400), nullable=False, default='')
    source_json: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False, default='candidate')
    version_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    validation_note: Mapped[str] = mapped_column(String(1000), nullable=False, default='')
