# -*- coding: utf-8 -*-
"""
common.models.base —— 模型基类与通用混入
========================================
本文件用途：为「拼多多自动回复」系统的全部数据表模型提供统一的
SQLAlchemy 2.0 声明式基类（Base）与通用列混入（Mixin），供按业务域拆分的
各模型文件（user_models / shop_models / reply_models / knowledge_models /
config_models / log_models / setting_models / task_models）共享复用。

关键约束（开发规范）：
- 规范 9：每张表必须有主键 —— 统一提供自增 BIGINT 主键 ``id``（IdMixin）。
- 规范 10：不使用外键约束 —— 表间关系一律由代码维护，模型中所有「关联键」
  （如 ``role_id`` / ``shop_pk`` / ``goods_id``）均为普通列，不声明 ForeignKey。
- 规范 17：全链路北京时间 —— 审计时间字段 ``created_at`` / ``updated_at``
  统一使用北京时间（Asia/Shanghai, UTC+8），默认值取 now_beijing_naive()。
- 通用审计字段：``created_at``（创建时间）、``updated_at``（更新时间，
  更新时自动刷新为当前北京时间）、``created_by``（创建人用户 id，普通列）。

设计说明：
- 采用 SQLAlchemy 2.0 ``DeclarativeBase`` + ``Mapped`` / ``mapped_column`` 风格。
- 主键统一为 ``BigInteger`` 自增（autoincrement），满足大体量数据需要。
- 时间字段类型为 ``DateTime``（库侧 default-time-zone=+08:00），存北京时间。
- 仅定义结构与元数据，不在此处建表；建表迁移自检由 backend 的 lifespan
  调用 SchemaMigrator 执行（任务 4.1 / 2.12）。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from common.utils.time_utils import now_beijing_naive


class Base(DeclarativeBase):
    """全部数据表模型的统一声明式基类。

    各业务域模型文件均继承自本 Base，使得所有表共享同一份
    ``Base.metadata``，便于启动自检迁移器（SchemaMigrator）统一建表。
    """


class IdMixin:
    """统一自增 BIGINT 主键混入（规范 9：每张表必须有主键）。

    所有业务表均通过继承本混入获得名为 ``id`` 的自增主键列，避免在每张表
    重复声明，保证主键风格一致。
    """

    # 统一主键：BIGINT 自增，满足大数据量与多表统一风格
    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
        comment="主键 ID（自增）",
    )


class TimestampMixin:
    """通用审计时间字段混入（规范 17：北京时间）。

    - ``created_at``：记录创建时间，默认当前北京时间；
    - ``updated_at``：记录更新时间，插入时默认当前北京时间，更新时自动刷新。

    时间值统一取 ``now_beijing_naive()``（去时区的北京时间），与数据库
    ``default-time-zone=+08:00`` 配合，保证全链路北京时间口径一致。
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=now_beijing_naive,
        nullable=False,
        comment="创建时间（北京时间）",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=now_beijing_naive,
        onupdate=now_beijing_naive,
        nullable=False,
        comment="更新时间（北京时间，更新时自动刷新）",
    )


class AuditMixin(IdMixin, TimestampMixin):
    """通用审计字段混入：主键 + 创建/更新时间 + 创建人。

    业务表继承本混入即可获得统一的 ``id`` 主键、``created_at`` /
    ``updated_at`` 北京时间审计字段，以及 ``created_by`` 创建人列。
    ``created_by`` 仅为普通列（记录创建人用户 id），不声明外键约束（规范 10）。
    """

    created_by: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
        comment="创建人用户 ID（普通列，关系由代码维护，不设外键）",
    )


__all__ = [
    "Base",
    "IdMixin",
    "TimestampMixin",
    "AuditMixin",
]
