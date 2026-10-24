# -*- coding: utf-8 -*-
"""
channel_pdd.api.get_conversations —— 拼多多会话列表拉取（在线聊天左侧列表）
=========================================================================
本文件用途：封装拼多多商家后台「最近会话列表」接口
``https://mms.pinduoduo.com/plateau/chat/latest_conversations``，供「在线聊天」
菜单拉取左侧会话列表（方案 A：实时调拼多多接口）。

接口机制（依据实测抓包）：
- 请求体 ``data`` 携带 ``cmd="latest_conversations"``、``page`` / ``size`` 分页、
  ``need_unreply_time``、``end_time`` 等，``data.anti_content`` 携带风控签名。
- 响应 ``result.conversations`` 返回每个客户的最近一条消息 + 用户信息
  （``user_info``：uid / nickname / avatar），``result.has_more`` 表示是否还有更多。

本模块仅做「拉取 + 规整」，不落库（会话明细由 get_chat_history 落库）。规整后每项
含客户 uid、昵称、头像、未读数、最近消息内容 / 类型 / 时间，供前端列表直接展示。

实现约束（开发规范）：继承 ``BaseRequest`` 复用请求 / 重试 / 重登 / 签名检测；
导入置顶（51）、中文注释（37）、文件名用下划线（40）、单文件 ≤500 行（35）、
日志禁用 debug（38）、全中文（50）。
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from channel_pdd.core.base_request import BaseRequest
from channel_pdd.pdd_message import Context, ContextType, PDDChatMessage
from common.utils.time_utils import safe_isoformat, utc_to_beijing

logger = logging.getLogger("channel_pdd.get_conversations")

# 最近会话列表查询接口地址（拼多多商家后台聊天平台）。
LATEST_CONVERSATIONS_URL: str = (
    "https://mms.pinduoduo.com/plateau/chat/latest_conversations"
)

# 单页默认条数（与实测前端一致）。
_DEFAULT_PAGE_SIZE: int = 100


class GetConversations(BaseRequest):
    """拼多多最近会话列表拉取（在线聊天左侧列表数据源）。

    按 (shop_id, user_id) 自数据库加载并解密 Cookie 后，拉取最近会话列表并规整为
    统一会话字典列表，供前端在线聊天菜单展示。
    """

    def __init__(
        self,
        shop_id: Optional[str] = None,
        user_id: Optional[int] = None,
        channel_name: str = "pinduoduo",
    ) -> None:
        """构造会话列表拉取实例。

        Args:
            shop_id: 拼多多店铺业务标识（与 user_id 同时提供时自数据库加载 Cookie）。
            user_id: 归属用户 ID。
            channel_name: 渠道名称（默认 pinduoduo）。
        """
        super().__init__(shop_id=shop_id, user_id=user_id, channel_name=channel_name)

    def _build_headers(self) -> Dict[str, str]:
        """构造聊天接口请求头（与发送消息 / 历史记录保持一致口径）。"""
        anti_content = self.cookies.get("anti_content") or self.cookies.get(
            "anti-content", ""
        )
        return {
            "accept": "application/json, text/plain, */*",
            "anti-content": anti_content,
            "content-type": "application/json;charset=UTF-8",
            "origin": "https://mms.pinduoduo.com",
            "referer": "https://mms.pinduoduo.com/chat-merchant/index.html",
        }

    def fetch(
        self, page: int = 1, size: int = _DEFAULT_PAGE_SIZE, *, recovery: bool = False
    ) -> Dict[str, Any]:
        """拉取一页最近会话列表（需求 14：在线聊天会话列表）。

        Args:
            page: 页码（从 1 开始）。
            size: 每页条数。

        Returns:
            ``{"success": bool, "conversations": [...规整后...], "has_more": bool}``；
            业务失败时 ``success=False`` 并附 ``error_msg``。
        """
        anti_content = self.cookies.get("anti_content") or self.cookies.get(
            "anti-content", ""
        )
        data = {
            "data": {
                "cmd": "latest_conversations",
                "request_id": self.generate_request_id(),
                "version": 2,
                "need_unreply_time": True,
                "page": page,
                "size": size,
                "anti_content": anti_content,
            },
            "client": 1,
        }

        result = self.post(
            LATEST_CONVERSATIONS_URL, json_data=data, headers=self._build_headers(),
            **({"retry_safe":False,"timeout":(3,8)} if recovery else {})
        )
        if result and result.get("success") is True:
            inner = result.get("result", {}) or {}
            if not isinstance(inner, dict) or not isinstance(inner.get("conversations"), list):
                return {"success": False, "error_msg": "平台会话响应结构异常，不能判定为没有会话", "conversations": [], "has_more": False}
            raw_list = inner["conversations"]
            conversations = [self._parse_conversation(raw) for raw in raw_list]
            # 仅记录数量与字段名，区分平台返回空与客户标识解析失败，不记录聊天内容或凭据。
            logger.info(
                "会话同步诊断: shop_id=%s owner_user_id=%s page=%s raw_count=%s parsed_uid_count=%s has_more=%s result_keys=%s first_item_keys=%s",
                self.shop_id, getattr(self, "user_id", None), page, len(raw_list),
                sum(bool(item.get("customer_uid")) for item in conversations),
                bool(inner.get("has_more")), sorted(inner),
                sorted(raw_list[0]) if raw_list and isinstance(raw_list[0], dict) else [],
            )
            return {
                "success": True,
                "conversations": conversations,
                "has_more": bool(inner.get("has_more")),
            }
        error_msg = (result or {}).get("error_msg") if result else "获取会话列表失败"
        logger.error("获取会话列表失败: shop_id=%s, %s", self.shop_id, error_msg)
        return {
            "success": False,
            "error_msg": error_msg,
            "auth_required": (self._is_session_expired(result) or getattr(self,"last_http_status",None) in (401,403)),
            "conversations": [],
            "has_more": False,
        }

    def fetch_all(self, size: int = _DEFAULT_PAGE_SIZE, *, recovery: bool = False, max_pages: int = 50) -> Dict[str, Any]:
        """拉取全部最近会话（按需翻页，直至 has_more=false）。

        Args:
            size: 每页条数。

        Returns:
            ``{"success": bool, "conversations": [...]}``；首页失败时 success=False。
        """
        first = self.fetch(page=1, size=size, **({"recovery":True} if recovery else {}))
        if not first.get("success"):
            return {
                "success": False,
                "error_msg": first.get("error_msg", "获取会话列表失败"),
                "auth_required":first.get("auth_required",False),
                "conversations": [],
            }
        conversations: List[Dict[str, Any]] = list(first.get("conversations", []))
        has_more = bool(first.get("has_more"))
        page, error = 2, ""
        while has_more and page <= max_pages:
            nxt = self.fetch(page=page, size=size, **({"recovery":True} if recovery else {}))
            if not nxt.get("success"):
                error = nxt.get("error_msg") or "会话翻页失败，请继续同步"
                break
            batch = nxt.get("conversations", [])
            conversations.extend(batch)
            has_more = bool(nxt.get("has_more"))
            if has_more and not batch:
                error = "平台返回空分页但仍有后续会话，请重试"
                break
            page += 1
        return {"success": True, "conversations": conversations, "complete": not has_more,
                "has_more": has_more, "next_cursor": page if has_more else None, "sync_error": error}

    # ------------------------------------------------------------------
    # 会话规整（复用 PDDChatMessage 解析最近一条消息，规范 36 / 52）
    # ------------------------------------------------------------------
    @classmethod
    def _parse_conversation(cls, raw: Dict[str, Any]) -> Dict[str, Any]:
        """将单条原始会话规整为统一字典（客户信息 + 最近一条消息摘要）。

        Args:
            raw: 原始会话字典（含 from/to/content/type/info/user_info/context 等）。

        Returns:
            统一会话字典：customer_uid / nickname / avatar / unread /
            last_content / last_msg_type / last_msg_id / last_ts。
        """
        # 复用 PDDChatMessage 解析最近一条消息语义（补 response=push 以走推送分发）。
        chat = PDDChatMessage({"response": "push", "message": raw})
        context: Context = chat.to_context()
        last_content = cls._resolve_content(context)

        user_info = raw.get("user_info") or {}
        # 客户 uid：优先取发送方为 user 的 uid，回退 user_info / from。
        customer_uid = cls._resolve_customer_uid(raw, user_info)
        context_info = raw.get("context") or {}
        last_ts = cls._to_int(raw.get("ts"))

        return {
            "customer_uid": customer_uid,
            "nickname": user_info.get("nickname") or chat.nickname,
            "avatar": user_info.get("avatar"),
            "unread": cls._to_int(context_info.get("unread")) or 0,
            "last_content": last_content,
            "last_direction": "in" if chat.from_user == "user" else "out",
            "last_msg_type": str(context.type) if context.type is not None else None,
            "last_msg_id": raw.get("msg_id"),
            "last_ts": last_ts,
            # 最近消息时间的北京时间字符串（供前端原样展示，规范 17）。
            "last_msg_at": cls._ts_to_beijing_str(last_ts),
        }

    @staticmethod
    def _resolve_customer_uid(
        raw: Dict[str, Any], user_info: Dict[str, Any]
    ) -> Optional[str]:
        """解析会话对应的客户 uid（发送方 / 接收方中角色为 user 的一方）。"""
        for key in ("from", "to"):
            party = raw.get(key) or {}
            if isinstance(party, dict) and party.get("role") == "user":
                uid = party.get("uid")
                if uid is not None:
                    return str(uid)
        uid = user_info.get("uid")
        return str(uid) if uid is not None else None

    @staticmethod
    def _resolve_content(context: Context) -> Optional[str]:
        """提取会话最近一条消息的展示文本（结构化消息回退可读占位）。"""
        if isinstance(context.content, str):
            return context.content
        if context.type == ContextType.GOODS_INQUIRY and context.goods_context:
            return f"[商品咨询] {context.goods_context.get('goods_name') or ''}".strip()
        if context.type == ContextType.GOODS_SPEC and context.goods_context:
            return f"[商品规格] {context.goods_context.get('goods_name') or ''}".strip()
        if context.type == ContextType.ORDER_INFO and context.order_context:
            return f"[订单] {context.order_context.get('order_id') or ''}".strip()
        return f"[{context.type}]" if context.type is not None else None

    @staticmethod
    def _to_int(value: Any) -> Optional[int]:
        """将值安全转为 int，失败返回 None。"""
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _ts_to_beijing_str(ts_int: Optional[int]) -> Optional[str]:
        """将秒级 epoch（UTC）转为北京时间 ISO 字符串（供前端原样展示，规范 17）。"""
        if not ts_int:
            return None
        return safe_isoformat(utc_to_beijing(datetime.utcfromtimestamp(ts_int)))


__all__ = ["GetConversations", "LATEST_CONVERSATIONS_URL"]
