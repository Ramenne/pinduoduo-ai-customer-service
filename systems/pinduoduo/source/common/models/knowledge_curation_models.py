"""人工处理结束后的持久提炼任务。结果仅为候选，不进入启用知识检索。"""
from sqlalchemy import BigInteger, Double, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.mysql import MEDIUMTEXT
from sqlalchemy.orm import Mapped, mapped_column
from common.models.base import AuditMixin, Base


class KnowledgeCuration(AuditMixin, Base):
    __tablename__ = 'pdd_knowledge_curation'
    __table_args__ = (UniqueConstraint('batch_key', name='uix_curation_batch'),)
    shop_pk: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    owner_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    customer_uid: Mapped[str] = mapped_column(String(128), nullable=False)
    batch_key: Mapped[str] = mapped_column(String(36), nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False, default='pending')
    snapshot_json: Mapped[str] = mapped_column(Text().with_variant(MEDIUMTEXT(), 'mysql'), nullable=False)
    result_json: Mapped[str | None] = mapped_column(Text().with_variant(MEDIUMTEXT(), 'mysql'))
    token: Mapped[str | None] = mapped_column(String(36))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    due_at: Mapped[float] = mapped_column(Double, nullable=False, default=0, index=True)
    error: Mapped[str | None] = mapped_column(String(128))
