# -*- coding: utf-8 -*-
"""
channel_pdd.api.get_chat_history —— 拼多多单会话历史聊天记录拉取（带循环翻页 + 落库）
===================================================================================
本文件用途：封装拼多多商家后台「会话历史消息」接口
``https://mms.pinduoduo.com/plateau/chat/list``，供「在线聊天」菜单按客户维度拉取
某个会话的全部历史聊天记录（方案 A：实时调拼多多接口）。

接口机制（依据实测抓包）：
- 请求体 ``data.list`` 携带 ``with={"role":"user","id":<uid>}`` 指定客户会话，
  ``start_msg_id`` 为游标（首次为 None 取最近一页），``size`` 为每页条数；
  ``data.anti_content`` 携带风控签名（取不到时以空串照常发起，由服务端裁决）。
- 响应 ``result.messages`` 按**时间倒序**返回本页消息，``result.has_more`` 表示
  是否还有更早的消息；翻页时以「本页最旧一条（即数组最后一条）的 msg_id」作为
  下一次 ``start_msg_id`` 继续往前拉，直至 ``has_more=false``。

本模块职责：
1. ``fetch_page``：拉取单页（薄封装，便于测试）。
2. ``fetch_all``：循环翻页拉取该会话「接口支持范围内」的全部历史，按时间**正序**
   返回解析后的消息列表（复用 ``PDDChatMessage`` 解析，规范 36 / 52）。
3. ``fetch_all_and_persist``：在 ``fetch_all`` 基础上将历史记录落库到
   ``conversation`` / ``chat_message``，按 ``(shop_pk, customer_uid, msg_id)`` 去重，
   只增不改不删（与实时收消息落库口径一致，规范 11）。

实现约束（开发规范）：继承 ``BaseRequest`` 复用请求 / 重试 / 重登 / 签名检测；
导入置顶（51）、中文注释（37）、文件名用下划线（40）、单文件 ≤500 行（35）、
日志禁用 debug（38）、全中文（50）。
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from channel_pdd.core.base_request import BaseRequest
from channel_pdd.pdd_message import Context, ContextType, PDDChatMessage
from common.db.repository import Repository, run_in_session
from common.models.log_models import ChatMessage, Conversation
from common.utils.time_utils import now_beijing_naive, safe_isoformat, utc_to_beijing

logger = logging.getLogger("channel_pdd.get_chat_history")

# 会话历史消息查询接口地址（拼多多商家后台聊天平台）。
CHAT_LIST_URL: str = "https://mms.pinduoduo.com/plateau/chat/list"

# 单页默认条数（与实测前端一致，取较小值降低单次负载）。
_DEFAULT_PAGE_SIZE: int = 50

# 循环翻页的最大页数保护（避免异常数据导致无限翻页）。
_MAX_PAGES: int = 200

# 翻页间隔（秒）：每翻一页前等待，规避拼多多频率风控。
_PAGE_INTERVAL_SECONDS: float = 0.5

# 消息方向常量（与 chat_message.direction 字段约定一致：in=收 / out=发）。
_DIRECTION_IN: str = "in"
_DIRECTION_OUT: str = "out"


class GetChatHistory(BaseRequest):
    """拼多多单会话历史聊天记录拉取（带循环翻页 + 可选落库）。

    按 (shop_id, user_id) 自数据库加载并解密 Cookie 后，按客户维度循环翻页拉取
    某会话的全部历史消息；可选将结果落库到本地会话 / 聊天消息表（按 msg_id 去重）。
    """

    def __init__(
        self,
        shop_id: Optional[str] = None,
        user_id: Optional[int] = None,
        channel_name: str = "pinduoduo",
    ) -> None:
        """构造历史聊天记录拉取实例。

        Args:
            shop_id: 拼多多店铺业务标识（与 user_id 同时提供时自数据库加载 Cookie）。
            user_id: 归属用户 ID。
            channel_name: 渠道名称（默认 pinduoduo）。
        """
        super().__init__(shop_id=shop_id, user_id=user_id, channel_name=channel_name)

    # ------------------------------------------------------------------
    # 请求构造
    # ------------------------------------------------------------------
    def _build_headers(self) -> Dict[str, str]:
        """构造聊天接口请求头（与发送消息 / 商品列表保持一致口径）。"""
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

    # ------------------------------------------------------------------
    # 单页拉取
    # ------------------------------------------------------------------
    def fetch_page(
        self,
        customer_uid: Any,
        start_msg_id: Optional[str] = None,
        size: int = _DEFAULT_PAGE_SIZE,
        *, recovery: bool = False,
    ) -> Dict[str, Any]:
        """拉取单页历史消息（薄封装，便于测试）。

        Args:
            customer_uid: 客户唯一标识。
            start_msg_id: 游标，传入「上一页最旧一条」的 msg_id 以往前翻；首次为 None。
            size: 每页条数。

        Returns:
            ``{"success": bool, "messages": [...原始消息...], "has_more": bool}``；
            业务失败时 ``success=False`` 并附 ``error_msg``。
        """
        # anti_content 放入请求体（依据实测：该接口签名在 data 内，而非仅请求头）。
        anti_content = self.cookies.get("anti_content") or self.cookies.get(
            "anti-content", ""
        )
        data = {
            "data": {
                "cmd": "list",
                "request_id": self.generate_request_id(),
                "list": {
                    "with": {"role": "user", "id": str(customer_uid)},
                    "start_msg_id": start_msg_id,
                    "start_index": 0,
                    "size": size,
                },
                "notUpdateUnreplyTs": True,
                "anti_content": anti_content,
            }
        }

        result = self.post(CHAT_LIST_URL, json_data=data, headers=self._build_headers(),
            **({"retry_safe":False,"timeout":(3,8)} if recovery else {}))
        if result and result.get("success") is True:
            inner = result.get("result", {}) or {}
            if not isinstance(inner, dict) or not isinstance(inner.get("messages"), list):
                return {"success": False, "error_msg": "平台历史响应结构异常，不能判定为空记录", "messages": [], "has_more": False}
            return {
                "success": True,
                "messages": inner["messages"],
                "has_more": bool(inner.get("has_more")),
            }
        error_msg = (result or {}).get("error_msg") if result else "获取聊天记录失败"
        logger.error(
            "获取聊天记录失败: shop_id=%s, customer=%s, %s",
            self.shop_id, customer_uid, error_msg,
        )
        return {"success": False, "auth_required":(self._is_session_expired(result) or getattr(self,"last_http_status",None) in (401,403)), "error_msg": error_msg, "messages": [], "has_more": False}

    # ------------------------------------------------------------------
    # 循环翻页拉取全部历史
    # ------------------------------------------------------------------
    def fetch_all(
        self, customer_uid: Any, size: int = _DEFAULT_PAGE_SIZE, *, cursor: Optional[str] = None, max_pages: int = _MAX_PAGES
    ) -> Dict[str, Any]:
        """循环翻页拉取该会话「接口支持范围内」的全部历史消息（按时间正序）。

        以「上一页最旧一条的 msg_id」为游标反复请求，直至 ``has_more=false`` 或达页
        数保护上限；按 msg_id 去重后按时间正序（旧 → 新）返回解析后的消息列表。

        Args:
            customer_uid: 客户唯一标识。
            size: 每页条数。

        Returns:
            ``{"success": bool, "messages": [{已解析的消息字典}...]}``；
            首页失败时 ``success=False`` 并附 ``error_msg``。
        """
        raw_messages: List[Dict[str, Any]] = []
        seen_msg_ids: set[str] = set()
        start_msg_id: Optional[str] = cursor
        has_more, sync_error = True, ""
        seen_cursors = {cursor} if cursor else set()

        for page_no in range(max(1, min(max_pages, _MAX_PAGES))):
            if page_no > 0:
                # 翻页前限流等待，规避频率风控。
                time.sleep(_PAGE_INTERVAL_SECONDS)
            page = self.fetch_page(customer_uid, start_msg_id=start_msg_id, size=size)
            if not page.get("success"):
                if page_no == 0:
                    return {
                        "success": False,
                        "error_msg": page.get("error_msg", "获取聊天记录失败"),
                        "messages": [],
                    }
                sync_error = page.get("error_msg") or "历史翻页失败，请继续同步"
                logger.warning(
                    "聊天记录第 %d 页拉取失败，提前结束: shop_id=%s, customer=%s",
                    page_no + 1, self.shop_id, customer_uid,
                )
                break

            batch = page.get("messages", [])
            if not batch:
                has_more = bool(page.get("has_more"))
                if has_more:
                    sync_error = "平台返回空分页但仍有历史记录，请重试"
                break

            # 去重累计（响应内每页倒序，最旧一条在数组末尾，作为下一页游标）。
            oldest_msg_id: Optional[str] = None
            for raw in batch:
                mid = self._extract_msg_id(raw)
                oldest_msg_id = mid  # 末次循环即为本页最旧一条
                if mid and mid in seen_msg_ids:
                    continue
                if mid:
                    seen_msg_ids.add(mid)
                raw_messages.append(raw)

            has_more = bool(page.get("has_more"))
            if not has_more:
                start_msg_id = None
                break
            if not oldest_msg_id or oldest_msg_id in seen_cursors:
                sync_error = "历史游标没有前进，请重试同步"
                break
            start_msg_id = oldest_msg_id
            seen_cursors.add(oldest_msg_id)

        # 按消息时间正序排列（旧 → 新），便于在线聊天与 AI 会话上下文使用。
        parsed = [self._parse_message(raw) for raw in raw_messages]
        parsed.sort(key=lambda m: m.get("ts") or 0)
        return {"success": True, "messages": parsed, "complete": not has_more, "has_more": has_more, "next_cursor": start_msg_id if has_more else None, "sync_error": sync_error}

    # ------------------------------------------------------------------
    # 拉取并落库
    # ------------------------------------------------------------------
    def fetch_all_and_persist(
        self, shop_pk: int, customer_uid: Any, size: int = _DEFAULT_PAGE_SIZE, *, cursor: Optional[str] = None, max_pages: int = _MAX_PAGES
    ) -> Dict[str, Any]:
        """循环翻页拉取全部历史并落库（按 msg_id 去重，只增不改不删）。

        在 ``fetch_all`` 之上，将历史消息 upsert 进本地 ``conversation`` /
        ``chat_message`` 表：已存在的 (shop_pk, customer_uid, msg_id) 跳过，避免与
        实时收消息落库重复；会话仅刷新最近消息时间与昵称（不改动未读数，避免历史
        回填污染未读计数）。落库失败不影响已拉取的数据返回（健壮性兜底）。

        Args:
            shop_pk: 本地店铺主键 shop.id。
            customer_uid: 客户唯一标识。
            size: 每页条数。

        Returns:
            ``{"success": bool, "messages": [...], "persisted": int}``；
            拉取失败时 ``success=False`` 并附 ``error_msg``。
        """
        result = self.fetch_all(customer_uid, size=size, **({"cursor": cursor, "max_pages": max_pages} if cursor is not None or max_pages != _MAX_PAGES else {}))
        if not result.get("success"):
            return {**result, "persisted": 0}

        messages = result.get("messages", [])
        try:
            persisted = self._persist_history(shop_pk, str(customer_uid), messages)
        except Exception as exc:  # noqa: BLE001 - 落库失败不影响数据返回
            logger.error(
                "聊天记录落库失败: shop_id=%s, customer=%s, %s",
                self.shop_id, customer_uid, exc,
            )
            return {**result, "persisted": 0, "complete": False, "storage_error": "历史记录保存失败，请重新同步", "has_more": True, "next_cursor": cursor}
        return {**result, "persisted": persisted}

    def _persist_history(
        self, shop_pk: int, customer_uid: str, messages: List[Dict[str, Any]], *, saved=None
    ) -> int:
        """将历史消息落库（去重 + upsert 会话），返回新增条数。

        Args:
            shop_pk: 本地店铺主键。
            customer_uid: 客户唯一标识。
            messages: 已解析并按时间正序的消息字典列表。

        Returns:
            实际新增的聊天消息条数。
        """
        if not messages:
            return 0

        def _do(session: Any) -> int:
            from sqlalchemy import select
            from common.models.shop_models import Shop
            # 与实时写入器按同一店铺行串行消重，不依赖进程内集合。
            session.scalar(select(Shop).where(Shop.id == shop_pk).with_for_update())
            chat_repo = Repository(ChatMessage, session)
            conv_repo = Repository(Conversation, session)
            inserted = 0
            latest_time: Optional[datetime] = None
            latest_nickname: Optional[str] = None

            for msg in messages:
                msg_id = msg.get("msg_id")
                # 按 (shop_pk, customer_uid, msg_id) 去重；无 msg_id 的历史消息跳过
                # 落库（无法稳定去重，避免重复回填）。
                if not msg_id:
                    continue
                exists = chat_repo.get_by(
                    shop_pk=shop_pk, customer_uid=customer_uid, msg_id=str(msg_id)
                )
                if exists is not None:
                    # 同一平台消息重新同步后，允许补齐曾被解析成占位符的商品信息。
                    # 不覆盖普通聊天原文、不改变时间或未读数，也不重复插入消息。
                    if exists.msg_type == "system_status" and msg.get("goods_context"):
                        chat_repo.update(exists.id, msg_type=msg.get("msg_type"), content=msg.get("content"),
                            goods_context=json.dumps(msg["goods_context"], ensure_ascii=False))
                    continue

                msg_time = msg.get("msg_time") or now_beijing_naive()
                chat_repo.create(
                    shop_pk=shop_pk,
                    customer_uid=customer_uid,
                    msg_id=str(msg_id),
                    direction=msg.get("direction") or _DIRECTION_IN,
                    msg_type=msg.get("msg_type"),
                    content=msg.get("content"),
                    order_context=(
                        json.dumps(msg["order_context"], ensure_ascii=False)
                        if msg.get("order_context")
                        else None
                    ),
                    goods_context=(
                        json.dumps(msg["goods_context"], ensure_ascii=False)
                        if msg.get("goods_context")
                        else None
                    ),
                    msg_time=msg_time,
                    created_by=self.user_id,
                )
                inserted += 1
                if saved is not None:
                    saved.append({k:v for k,v in msg.items() if k != 'msg_time'})
                if latest_time is None or msg_time >= latest_time:
                    latest_time = msg_time
                    if msg.get("nickname"):
                        latest_nickname = msg.get("nickname")

            # upsert 会话：仅刷新最近消息时间与昵称，不改动未读数（历史回填）。
            if latest_time is not None:
                conversation = conv_repo.get_by(
                    shop_pk=shop_pk, customer_uid=customer_uid
                )
                if conversation is None:
                    conv_repo.create(
                        shop_pk=shop_pk,
                        customer_uid=customer_uid,
                        nickname=latest_nickname,
                        last_msg_at=latest_time,
                        unread_count=0,
                    )
                else:
                    update_values: Dict[str, Any] = {}
                    if (
                        conversation.last_msg_at is None
                        or latest_time > conversation.last_msg_at
                    ):
                        update_values["last_msg_at"] = latest_time
                    if latest_nickname and not conversation.nickname:
                        update_values["nickname"] = latest_nickname
                    if update_values:
                        conv_repo.update(conversation.id, **update_values)

            return inserted

        return run_in_session(_do)

    # ------------------------------------------------------------------
    # 消息解析（复用 PDDChatMessage，规范 36 / 52）
    # ------------------------------------------------------------------
    @staticmethod
    def _extract_msg_id(raw: Dict[str, Any]) -> Optional[str]:
        """从原始消息字典中提取 msg_id（缺失返回 None）。"""
        if not isinstance(raw, dict):
            return None
        mid = raw.get("msg_id")
        return str(mid) if mid is not None else None

    @classmethod
    def _parse_message(cls, raw: Dict[str, Any]) -> Dict[str, Any]:
        """将单条原始历史消息解析为统一字典（复用 PDDChatMessage）。

        chat/list 返回的单条消息结构与 WebSocket 推送的 push 消息一致，但缺少
        ``response`` 字段；这里补 ``response="push"`` 以复用既有解析分发逻辑，
        从而正确还原文本 / 商品 / 订单等语义与上下文。

        Args:
            raw: 原始历史消息字典（含 from/to/content/type/info/msg_id/ts 等）。

        Returns:
            统一消息字典：含 direction / msg_type / content / order_context /
            goods_context / msg_id / nickname / ts / msg_time。
        """
        # chat/list 的每条消息把字段平铺在顶层（from/to/content/type/info...），
        # 而 PDDChatMessage 期望 message 子层 + response 分发；包一层适配。
        wrapped = {"response": "push", "message": raw}
        chat = PDDChatMessage(wrapped)
        context: Context = chat.to_context()

        # 方向：发送方为 user → 收（in）；mall_cs → 发（out）。
        from_role = chat.from_user
        direction = _DIRECTION_IN if from_role == "user" else _DIRECTION_OUT

        # 文本内容：结构化消息（商品 / 订单）取占位文案，便于列表展示。
        content = cls._resolve_content(context)

        # 时间戳：拼多多 ts 为秒级 epoch（UTC）；统一换算为北京时间 naive 供落库
        # 与展示（规范 17：全链路北京时间，避免依赖服务器本地时区）。
        ts_raw = raw.get("ts") or chat.timestamp
        ts_int = cls._to_int(ts_raw)
        msg_time = (
            utc_to_beijing(datetime.utcfromtimestamp(ts_int)).replace(tzinfo=None)
            if ts_int
            else now_beijing_naive()
        )

        return {
            "msg_id": chat.msg_id or cls._extract_msg_id(raw),
            "direction": direction,
            "msg_type": str(context.type) if context.type is not None else None,
            "content": content,
            "order_context": context.order_context or None,
            "goods_context": context.goods_context or None,
            "nickname": chat.nickname,
            "from_uid": chat.from_uid,
            "ts": ts_int,
            # 北京时间字符串（供前端原样展示，规范 17）与 naive datetime（供落库）。
            "msg_at": safe_isoformat(msg_time),
            "msg_time": msg_time,
        }

    @staticmethod
    def _resolve_content(context: Context) -> Optional[str]:
        """提取用于展示 / 落库的文本内容（结构化消息回退为可读占位）。"""
        if isinstance(context.content, str):
            return context.content
        if context.type in (ContextType.IMAGE,ContextType.VIDEO) and isinstance(context.content,(dict,list)):
            import json
            return json.dumps(context.content,ensure_ascii=False)
        if context.type == ContextType.GOODS_INQUIRY and context.goods_context:
            return f"[商品咨询] {context.goods_context.get('goods_name') or ''}".strip()
        if context.type == ContextType.GOODS_SPEC and context.goods_context:
            return f"[商品规格] {context.goods_context.get('goods_name') or ''}".strip()
        if context.type == ContextType.ORDER_INFO and context.order_context:
            return f"[订单] {context.order_context.get('order_id') or ''}".strip()
        # 其它非文本类型（图片 / 视频 / 系统消息等）：用类型名占位。
        return f"[{context.type}]" if context.type is not None else None

    @staticmethod
    def _to_int(value: Any) -> Optional[int]:
        """将秒级时间戳（字符串 / 数字）安全转为 int，失败返回 None。"""
        try:
            return int(value)
        except (TypeError, ValueError):
            return None


__all__ = ["GetChatHistory", "CHAT_LIST_URL"]
