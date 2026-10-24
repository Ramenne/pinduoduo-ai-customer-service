"""按店铺+稳定客户ID保存自动/人工归属，不依赖页面或进程生命周期。"""
from sqlalchemy import BigInteger, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.mysql import MEDIUMTEXT
from sqlalchemy.orm import Mapped, mapped_column
from common.models.base import AuditMixin, Base


class ConversationControl(AuditMixin, Base):
    __tablename__ = 'pdd_conversation_control'
    __table_args__ = (UniqueConstraint('shop_pk', 'customer_uid', name='uix_conversation_control'),)

    shop_pk: Mapped[int] = mapped_column(BigInteger, nullable=False)
    customer_uid: Mapped[str] = mapped_column(String(128), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    reason: Mapped[str | None] = mapped_column(String(128), nullable=True)

    supplement_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    draft_json: Mapped[str | None] = mapped_column(Text().with_variant(MEDIUMTEXT(), 'mysql'), nullable=True)
    workspace_json: Mapped[str | None] = mapped_column(Text().with_variant(MEDIUMTEXT(), 'mysql'), nullable=True)
    harness_json: Mapped[str | None] = mapped_column(Text().with_variant(MEDIUMTEXT(), 'mysql'), nullable=True)
    attention_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
