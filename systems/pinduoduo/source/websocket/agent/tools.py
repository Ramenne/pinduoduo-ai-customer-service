# -*- coding: utf-8 -*-
"""
websocket.agent.tools —— AI 回复引擎工具集（经 common kb.search 检索）
=====================================================================
本文件用途：复用改造 Customer-Agent 的工具 ``get_product_knowledge`` 与
``search_customer_service_knowledge``，实现 websocket 服务 AI 回复引擎可调用的
知识库检索工具。两个工具均经 common 公共库 ``kb_service.search`` 检索（限定店铺、
客服知识仅启用、结果不超 limit、按 goods_id 精确匹配、jieba 分词），并将检索
结果格式化为「Agent 可读的中文文本」回传给 LLM（需求 8.2 / 8.3 / 8.4）。

设计要点：
- 工具不直接持有数据库会话，而是在调用时通过 ``common.db.session.session_scope``
  打开短事务执行检索（只读），调用结束即释放连接；
- 每个工具提供 OpenAI 风格的「function schema」（``*_SCHEMA``），由 Agent 循环
  汇总后传给 LLM；工具的实际执行入口为 ``TOOL_REGISTRY`` 中的可调用对象；
- 工具入参由 LLM 给出，需经校验（缺失 shop_id / goods_id / query 时返回中文
  错误提示，而非抛异常中断 Agent 循环）；
- ``shop_id`` 对应店铺主键 shop.id（与 kb.search 的 ``shop_pk`` 一致）。

复用约束（开发规范 36 / 52）：知识检索逻辑复用 common 的 kb.search，本模块仅做
「参数校验 + 调用 + 结果格式化」，不重复实现检索与分词逻辑。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from agent.product_catalog import list_shop_products, product_details
from common.db.session import session_scope
from common.services import kb_service
from common.services.kb_service import KbSearchResult

# 单条知识内容在格式化文本中的最大长度（超出截断，避免 Prompt 过长）。
_PRODUCT_CONTENT_MAX: int = 500
_CS_CONTENT_MAX: int = 300

# 工具名常量（与 LLM function schema 的 name 对齐）。
TOOL_GET_PRODUCT_KNOWLEDGE: str = "get_product_knowledge"
TOOL_SEARCH_CS_KNOWLEDGE: str = "search_customer_service_knowledge"


# ----------------------------------------------------------------------
# 结果格式化（复用参照项目 format_search_result 思路，输出中文文本）
# ----------------------------------------------------------------------
def _truncate(text: Optional[str], max_len: int) -> str:
    """将文本截断到指定长度（超出追加省略号）。"""
    if not text:
        return ""
    content = str(text)
    if len(content) > max_len:
        return content[:max_len] + "..."
    return content


def format_search_result(result: KbSearchResult) -> str:
    """将 kb.search 检索结果格式化为 Agent 可读的中文文本。

    Args:
        result: kb.search 返回的检索结果（商品知识 + 客服知识）。

    Returns:
        格式化后的中文文本；无任何命中时返回「未找到相关知识。」。
    """
    parts: List[str] = []

    products = result.product_knowledge or []
    if products:
        parts.append("【产品知识】")
        for i, p in enumerate(products, 1):
            info = [f"{i}. {getattr(p, 'goods_name', '') or ''}（商品ID: {getattr(p, 'goods_id', '')}）"]
            price = getattr(p, "price", None)
            if price is not None:
                info.append(f"  价格: {price}")
            extracted = getattr(p, "extracted_content", None)
            if extracted:
                info.append(f"  {_truncate(extracted, _PRODUCT_CONTENT_MAX)}")
            parts.append("\n".join(info))
            parts.append("")

    cs_list = result.customer_service_knowledge or []
    if cs_list:
        parts.append("【客服知识】")
        for i, cs in enumerate(cs_list, 1):
            info = [f"{i}. {getattr(cs, 'title', '') or ''}"]
            info.append(f"  {_truncate(getattr(cs, 'content', ''), _CS_CONTENT_MAX)}")
            parts.append("\n".join(info))
            parts.append("")

    if not parts:
        return "未找到相关知识。"
    return "\n".join(parts).strip()


# ----------------------------------------------------------------------
# 工具实现：商品知识检索（需求 8.2）
# ----------------------------------------------------------------------
def get_product_knowledge(params: Dict[str, Any]) -> str:
    """获取指定商品的详细知识（经 common kb.search 按 goods_id 精确匹配）。

    Args:
        params: 工具参数字典，需含 ``shop_id`` 与 ``goods_id``。

    Returns:
        格式化的中文商品知识文本；参数缺失时返回中文错误提示。
    """
    shop_id = params.get("shop_id")
    goods_id = params.get("goods_id")
    if shop_id in (None, "", 0):
        return "[错误：缺少店铺ID，无法获取产品知识]"
    if goods_id in (None, ""):
        return "[错误：缺少商品ID，无法获取产品知识]"

    with session_scope() as session:
        result = kb_service.search(
            session=session,
            shop_pk=int(shop_id),
            query=None,
            goods_id=str(goods_id),
        )
        text = format_search_result(result)
    if not result.product_knowledge:
        text = product_details(shop_id, goods_id) + ("\n" + text if result.customer_service_knowledge else "")
    return text


# ----------------------------------------------------------------------
# 工具实现：客服知识检索（需求 8.3）
# ----------------------------------------------------------------------
def search_customer_service_knowledge(params: Dict[str, Any]) -> str:
    """搜索客服知识库（经 common kb.search 对 query 分词匹配启用记录）。

    Args:
        params: 工具参数字典，需含 ``shop_id`` 与 ``query``。

    Returns:
        格式化的中文客服知识文本；参数缺失时返回中文错误提示。
    """
    shop_id = params.get("shop_id")
    query = params.get("query")
    if shop_id in (None, "", 0):
        return "[错误：缺少店铺ID，无法搜索客服知识]"
    if not query or not str(query).strip():
        return "[错误：缺少搜索关键词，无法搜索客服知识]"

    with session_scope() as session:
        result = kb_service.search(
            session=session,
            shop_pk=int(shop_id),
            query=str(query),
            goods_id=None,
        )
        return format_search_result(result)


# ----------------------------------------------------------------------
# 工具的 OpenAI function schema（提供给 LLM 做 function calling）
# ----------------------------------------------------------------------
GET_PRODUCT_KNOWLEDGE_SCHEMA: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": TOOL_GET_PRODUCT_KNOWLEDGE,
        "description": (
            "获取指定商品的详细知识信息，包括规格、价格、抽取内容等。"
            "当用户询问特定商品时使用此工具。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "goods_id": {"type": "string", "description": "商品ID（必须提供）"},
                "shop_id": {"type": "integer", "description": "店铺ID（必须提供）"},
            },
            "required": ["goods_id", "shop_id"],
        },
    },
}

SEARCH_CS_KNOWLEDGE_SCHEMA: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": TOOL_SEARCH_CS_KNOWLEDGE,
        "description": (
            "搜索客服知识库，查找售后政策、常见问题、物流与退换货等知识。"
            "当用户询问售后、物流、退款等非商品特定问题时使用此工具。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词（必须提供）"},
                "shop_id": {"type": "integer", "description": "店铺ID（必须提供）"},
            },
            "required": ["query", "shop_id"],
        },
    },
}

LIST_SHOP_PRODUCTS_SCHEMA = {
    "type": "function", "function": {
        "name": "list_shop_products",
        "description": "读取本店已同步商品列表，按商品名称搜索。客户没有提供商品ID或询问本店卖什么时先使用；结果不代表实时库存。",
        "parameters": {"type": "object", "properties": {
            "shop_id": {"type": "integer"}, "query": {"type": "string", "description": "可选商品名称关键词"}
        }, "required": ["shop_id"]}
    }
}

# 工具注册表：工具名 -> 可调用执行入口（入参为参数字典，返回中文文本）。
TOOL_REGISTRY: Dict[str, Any] = {
    "list_shop_products": list_shop_products,
    TOOL_GET_PRODUCT_KNOWLEDGE: get_product_knowledge,
    TOOL_SEARCH_CS_KNOWLEDGE: search_customer_service_knowledge,
}

# 工具 schema 列表：供 Agent 循环传给 LLM。
TOOL_SCHEMAS: List[Dict[str, Any]] = [
    LIST_SHOP_PRODUCTS_SCHEMA,
    GET_PRODUCT_KNOWLEDGE_SCHEMA,
    SEARCH_CS_KNOWLEDGE_SCHEMA,
]


def execute_tool(name: str, params: Dict[str, Any]) -> str:
    """按工具名执行对应工具，返回中文文本结果。

    Args:
        name: 工具名（须在 TOOL_REGISTRY 中）。
        params: 工具参数字典。

    Returns:
        工具执行的中文文本结果；未知工具返回中文错误提示。
    """
    tool = TOOL_REGISTRY.get(name)
    if tool is None:
        return f"[错误：未知工具 {name}]"
    return tool(params or {})


__all__ = [
    "TOOL_GET_PRODUCT_KNOWLEDGE",
    "TOOL_SEARCH_CS_KNOWLEDGE",
    "GET_PRODUCT_KNOWLEDGE_SCHEMA",
    "SEARCH_CS_KNOWLEDGE_SCHEMA",
    "TOOL_REGISTRY",
    "TOOL_SCHEMAS",
    "format_search_result",
    "get_product_knowledge",
    "search_customer_service_knowledge",
    "execute_tool",
]
