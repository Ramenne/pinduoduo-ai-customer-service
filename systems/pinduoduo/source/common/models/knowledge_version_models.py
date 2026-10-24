"""统一知识历史：只追加快照，原知识表仍是当前有效内容。"""
from sqlalchemy import BigInteger, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.mysql import MEDIUMTEXT
from sqlalchemy.orm import Mapped, mapped_column
from common.models.base import AuditMixin, Base


class KnowledgeVersion(AuditMixin, Base):
    __tablename__ = 'pdd_knowledge_version'
    __table_args__ = (UniqueConstraint('kind', 'item_id', 'version_no', name='uix_knowledge_version'),)
    shop_pk: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    item_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text().with_variant(MEDIUMTEXT(), 'mysql'), nullable=False)
    operation: Mapped[str] = mapped_column(String(32), nullable=False)
    source: Mapped[str | None] = mapped_column(String(128), nullable=True)
