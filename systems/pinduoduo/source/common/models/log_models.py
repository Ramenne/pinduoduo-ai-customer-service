# -*- coding: utf-8 -*-
"""
common.models.log_models —— 会话与日志数据表模型
================================================
本文件用途：定义「拼多多自动回复」系统会话与日志业务域的数据表结构模型，覆盖：
- conversation   会话（客户维度，最近消息时间 / 未读数）
- chat_message   聊天消息（方向 / 类型 / 内容 / 订单与商品上下文 / 消息时间）
- message_log    消息处理日志（禁止物理删除，需求 19.5）
- risk_log       风控日志（禁止物理删除）
- system_log     系统日志（级别 / 模块 / 内容）
- notify_record  通知发送记录

关键约束（开发规范）：
- 规范 9：每张表均有自增 BIGINT 主键。
- 规范 10：shop_pk / channel_id 等关联键均为普通列，无外键。
- 规范 11 / 需求 19.5：message_log / risk_log 等业务日志禁止物理删除。
- 规范 17：全部时间字段（last_msg_at / msg_time / log_time）统一北京时间。
- 消息方向（收/发）、消息处理结果、风控类型、售后状态/类型等枚举入字典。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from common.models.base import AuditMixin, Base


class Conversation(AuditMixin, Base):
    """会话表 conversation。

    以客户维度聚合会话，记录最近消息时间与未读数，供会话列表展示。
    """

    __tablename__ = "pdd_conversation"
    __table_args__ = {"comment": "会话表（店铺 / 客户会话基础信息）"}

    shop_pk: Mapped[int] = mapped_column(
        BigInteger, nullable=False, comment="关联店铺主键 shop.id（普通列，无外键）"
    )
    customer_uid: Mapped[str] = mapped_column(
        String(128), nullable=False, comment="客户唯一标识 customer_uid"
    )
    nickname: Mapped[str | None] = mapped_column(
        String(255), nullable=True, comment="客户昵称"
    )
    last_msg_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, comment="最近消息时间（北京时间，用于倒序）"
    )
    unread_count: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False, comment="未读消息数"
    )


class ChatMessage(AuditMixin, Base):
    """聊天消息表 chat_message。

    存储收发的聊天消息及其订单 / 商品上下文（JSON 文本），供在线聊天展示。
    消息方向、消息类型枚举入字典（需求 17.x）。
    """

    __tablename__ = "pdd_chat_message"
    __table_args__ = {"comment": "聊天消息表（会话消息 / 方向 / 类型 / 上下文）"}

    shop_pk: Mapped[int] = mapped_column(
        BigInteger, nullable=False, comment="关联店铺主键 shop.id（普通列，无外键）"
    )
    customer_uid: Mapped[str] = mapped_column(
        String(128), nullable=False, comment="客户唯一标识 customer_uid"
    )
    # 拼多多消息唯一 ID：用于「实时收消息」与「历史记录回填」落库去重（同一 msg_id
    # 在同一会话内只保留一条）；本系统自发回复无 msg_id，置空不参与去重。
    msg_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True, comment="拼多多消息 ID（落库去重用，可空）"
    )
    # 消息方向：in=收 / out=发（枚举入字典）
    direction: Mapped[str] = mapped_column(
        String(16), nullable=False, comment="消息方向：in=收/out=发（枚举入字典）"
    )
    msg_type: Mapped[str | None] = mapped_column(
        String(32), nullable=True, comment="消息类型（文本/图片/视频/表情等，枚举入字典）"
    )
    content: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="消息内容"
    )
    # 订单上下文：JSON 文本（订单号/商品名/goods_id/规格/售后状态/售后类型）
    order_context: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="订单上下文（JSON 文本）"
    )
    # 商品上下文：JSON 文本（goods_id/商品名/价格/缩略图）
    goods_context: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="商品上下文（JSON 文本）"
    )
    msg_time: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, comment="消息时间（北京时间）"
    )


class MessageLog(AuditMixin, Base):
    """消息处理日志表 message_log。

    记录每条消息的处理结果与回复内容。**禁止物理删除**（需求 19.5，规范 11）。
    """

    __tablename__ = "pdd_message_log"
    __table_args__ = {"comment": "消息日志表（消息处理记录 / 处理结果）"}

    shop_pk: Mapped[int] = mapped_column(
        BigInteger, nullable=False, comment="关联店铺主键 shop.id（普通列，无外键）"
    )
    customer_uid: Mapped[str | None] = mapped_column(
        String(128), nullable=True, comment="客户唯一标识 customer_uid"
    )
    message_content: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="原始消息内容"
    )
    # 处理结果：枚举入字典（如 自动回复/AI回复/已过滤/非营业时间/AI回复失败 等）
    process_result: Mapped[str | None] = mapped_column(
        String(64), nullable=True, comment="处理结果（枚举入字典）"
    )
    reply_content: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="回复内容"
    )
    log_time: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, comment="日志时间（北京时间）"
    )


class RiskLog(AuditMixin, Base):
    """风控日志表 risk_log。

    记录风控触发事件。**禁止物理删除**（需求 19.5，规范 11）。
    风控类型枚举入字典（需求 13.4）。
    """

    __tablename__ = "pdd_risk_log"
    __table_args__ = {"comment": "风控日志表（风控触发记录 / 类型 / 原因）"}

    shop_pk: Mapped[int] = mapped_column(
        BigInteger, nullable=False, comment="关联店铺主键 shop.id（普通列，无外键）"
    )
    # 风控类型：枚举入字典（如 频率限制/重连失败 等）
    risk_type: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="风控类型（枚举入字典）"
    )
    trigger_reason: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="触发原因"
    )
    log_time: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, comment="日志时间（北京时间）"
    )


class SystemLog(AuditMixin, Base):
    """系统日志表 system_log。

    记录系统级事件（级别 / 模块 / 内容）。注意日志级别禁止使用 debug（规范 38）。
    """

    __tablename__ = "pdd_system_log"
    __table_args__ = {"comment": "系统日志表（级别 / 模块 / 内容，禁用 debug）"}

    # 日志级别：info/warning/error（禁止 debug，规范 38）
    level: Mapped[str] = mapped_column(
        String(16), nullable=False, comment="日志级别：info/warning/error"
    )
    module: Mapped[str | None] = mapped_column(
        String(128), nullable=True, comment="来源模块"
    )
    content: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="日志内容"
    )
    log_time: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, comment="日志时间（北京时间）"
    )


class NotifyRecord(AuditMixin, Base):
    """通知发送记录表 notify_record。

    记录系统事件通知的发送结果，发送失败记日志不中断主流程（需求 18.4）。
    """

    __tablename__ = "pdd_notify_record"
    __table_args__ = {"comment": "通知记录表（渠道 / 事件类型 / 发送结果）"}

    # 归属店铺主键：普通列，无外键（规范 10）。可空兼容历史数据（启动自检自动补列）。
    shop_pk: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, index=True, comment="归属店铺主键 shop.id（普通列，无外键）"
    )
    # 通知渠道 id：普通列，无外键
    channel_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, comment="通知渠道 ID（普通列，无外键）"
    )
    event_type: Mapped[str | None] = mapped_column(
        String(64), nullable=True, comment="事件类型（连接断开/登录态失效/风控触发等）"
    )
    content: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="通知内容"
    )
    send_result: Mapped[str | None] = mapped_column(
        String(32), nullable=True, comment="发送结果（成功/失败）"
    )
    log_time: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, comment="日志时间（北京时间）"
    )


__all__ = [
    "Conversation",
    "ChatMessage",
    "MessageLog",
    "RiskLog",
    "SystemLog",
    "NotifyRecord",
]
