"""商品后台同步的持久状态；同店铺一条记录，避免重复同步叠加。"""
from datetime import datetime
from sqlalchemy import BigInteger, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from common.models.base import AuditMixin, Base


class ProductSyncJob(AuditMixin, Base):
    __tablename__ = 'pdd_product_sync_job'

    shop_pk: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True)
    product_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False)
    run_token: Mapped[str] = mapped_column(String(64), nullable=False)
    owner_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    total: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    completed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
