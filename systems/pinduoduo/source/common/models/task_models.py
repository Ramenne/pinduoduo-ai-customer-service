# -*- coding: utf-8 -*-
"""
common.models.task_models —— 公告、反馈与定时任务数据表模型
==========================================================
本文件用途：定义「拼多多自动回复」系统管理端功能业务域的数据表结构模型，覆盖：
- announcement    公告（标题/正文/启停用/发布时间/发布人/逻辑删除标记）
- feedback        意见反馈（用户提交内容/联系方式/处理状态/管理员回复）
- scheduled_task  定时任务（任务键/调度方式/调度配置/上次与下次执行时间）
- task_run_log    定时任务执行日志（执行结果/信息/执行时间，禁止物理删除）

关键约束（开发规范）：
- 规范 9：每张表均有自增 BIGINT 主键。
- 规范 10：user_id / created_by 等关联键均为普通列，无外键。
- 规范 11 / 需求 19.5 / 21.3：公告逻辑删除、task_run_log 禁止物理删除。
- 规范 17：全部时间字段（publish_at / last_run_at / next_run_at / log_time）
  统一北京时间。
- 反馈处理状态、定时任务调度方式与执行结果等枚举入字典（需求 21.5 / 21.2）。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from common.models.base import AuditMixin, Base


class Announcement(AuditMixin, Base):
    """公告表 announcement。

    支持多条公告与列表查询；删除 / 下线通过 ``status`` 或 ``deleted_flag`` 逻辑
    实现，禁止物理删除（需求 21.3，规范 11）。``created_by`` 即发布人（继承自
    AuditMixin），``publish_at`` 为发布时间（北京时间）。
    """

    __tablename__ = "pdd_announcement"
    __table_args__ = {"comment": "公告表（标题 / 内容 / 启停用 / 逻辑删除）"}

    title: Mapped[str] = mapped_column(
        String(255), nullable=False, comment="公告标题"
    )
    content: Mapped[str] = mapped_column(
        Text, nullable=False, comment="公告正文"
    )
    # 状态：1=启用（上线）/ 0=停用（下线），逻辑控制
    status: Mapped[int] = mapped_column(
        Integer, default=1, nullable=False, comment="状态：1=启用/0=停用（逻辑下线）"
    )
    publish_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, comment="发布时间（北京时间）"
    )
    # 逻辑删除标记：True=已删除（仍保留数据，禁止物理删除）
    deleted_flag: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, comment="逻辑删除标记（True=已删除，仍保留）"
    )


class Feedback(AuditMixin, Base):
    """意见反馈表 feedback。

    用户提交反馈落库，管理员查看并处理回复。处理状态枚举入字典（需求 21.5）。
    """

    __tablename__ = "pdd_feedback"
    __table_args__ = {"comment": "意见反馈表（提交内容 / 处理状态 / 管理员回复）"}

    # 提交用户 id：普通列，无外键
    user_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="提交用户 ID（普通列，无外键）"
    )
    content: Mapped[str] = mapped_column(
        Text, nullable=False, comment="反馈内容"
    )
    contact: Mapped[str | None] = mapped_column(
        String(128), nullable=True, comment="联系方式"
    )
    # 处理状态：pending/processing/done/closed（枚举入字典）
    status: Mapped[str] = mapped_column(
        String(32),
        default="pending",
        nullable=False,
        comment="处理状态：pending/processing/done/closed（枚举入字典）",
    )
    reply: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="管理员回复"
    )


class ScheduledTask(AuditMixin, Base):
    """定时任务表 scheduled_task。

    SchedulerService 按本表配置调度任务（Cookie 刷新 / 商品同步 / 文件日志清理）。
    调度方式枚举入字典；时间字段为北京时间（需求 21.2）。
    """

    __tablename__ = "pdd_scheduled_task"
    __table_args__ = {"comment": "定时任务表（任务键 / 调度方式 / 调度配置 / 启停用）"}

    # 任务键：如 cookie_refresh / product_sync / log_file_cleanup
    task_key: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="任务键（cookie_refresh/product_sync 等）"
    )
    task_name: Mapped[str] = mapped_column(
        String(128), nullable=False, comment="任务名称（中文）"
    )
    # 调度方式：cron / interval（枚举入字典）
    schedule_type: Mapped[str] = mapped_column(
        String(16), nullable=False, comment="调度方式：cron/interval（枚举入字典）"
    )
    # 调度配置：cron 表达式或间隔配置
    schedule_config: Mapped[str | None] = mapped_column(
        String(255), nullable=True, comment="调度配置（cron 表达式或间隔）"
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False, comment="是否启用"
    )
    last_run_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, comment="上次执行时间（北京时间）"
    )
    next_run_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, comment="下次执行时间（北京时间）"
    )


class TaskRunLog(AuditMixin, Base):
    """定时任务执行日志表 task_run_log。

    记录定时任务每次执行结果。属于业务数据，**禁止物理删除**（规范 11，需求
    19.5），与「日志保留天数」配置无关。
    """

    __tablename__ = "pdd_task_run_log"
    __table_args__ = {"comment": "定时任务执行日志表（任务键 / 执行结果 / 耗时）"}

    task_key: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="任务键"
    )
    # 执行结果：success/failed（枚举入字典）
    run_result: Mapped[str] = mapped_column(
        String(16), nullable=False, comment="执行结果：success/failed（枚举入字典）"
    )
    message: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="执行信息"
    )
    log_time: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, comment="执行时间（北京时间）"
    )


__all__ = [
    "Announcement",
    "Feedback",
    "ScheduledTask",
    "TaskRunLog",
]
