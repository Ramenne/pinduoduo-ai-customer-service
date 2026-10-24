# -*- coding: utf-8 -*-
"""
websocket.engine.keyword_matcher —— 关键词规则按优先级匹配（纯逻辑）
==================================================================
本文件用途：实现 websocket 自动回复引擎运行时的「关键词规则匹配」纯逻辑，
供 reply_engine 决策链复用，满足需求 6：

- 需求 6.2：收到客户消息且自动回复启用时，按规则优先级匹配关键词规则。
- 需求 6.3：客户消息命中某关键词规则时，返回该规则配置的回复内容。
- 需求 6.4：同一消息命中多条规则时，仅返回优先级最高的一条规则的回复内容。
- 需求 6.5：规则为图片回复类型时，返回该规则配置的图片消息。
- 需求 6.7（联动）：停用规则（enabled=False）不参与匹配。

匹配方式（与 backend keyword_service / sys_dict 的 match_type 字典一致）：
- full     全匹配：消息文本与关键词完全相等（去除两端空白后比较）即命中；
- contains 包含：关键词为消息文本的子串即命中；
- regex    正则：关键词作为正则在消息文本中搜索到即命中（非法正则视为不命中，
           避免运行时因配置错误而抛异常中断会话）。

优先级与唯一命中（需求 6.4）：
- 规则 priority 数值越大优先级越高；
- 命中多条时仅返回 priority 最高的一条；
- priority 相同（并列最高）时，取入参顺序靠前者，保证结果确定可测。

回复类型（需求 6.5）：
- reply_type 为枚举键（text 文本 / image 图片），随命中规则原样返回，由上层
  据此发送文本或图片消息。

设计要点：
- 纯逻辑、无 I/O：关键词规则由上层（从持久化层）传入，元素可为模型对象
  （KeywordRule）或普通 dict；本模块仅做匹配判定，不访问数据库。
- 仅启用规则参与匹配（enabled=True，需求 6.7）。

实现约束（开发规范）：
- 导入置顶（规范 51）；中文注释（规范 37）；单文件 ≤500 行（规范 35）；
  文件名用下划线（规范 40）；全中文（规范 50）；与共通库复用 KeywordRule 模型
  （规范 52）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Optional

# 匹配方式常量（与 backend keyword_service.ALLOWED_MATCH_TYPES 一致）。
MATCH_FULL: str = "full"  # 全匹配：消息文本与关键词完全相等
MATCH_CONTAINS: str = "contains"  # 包含匹配：关键词为消息文本子串
MATCH_REGEX: str = "regex"  # 正则匹配：关键词作为正则在消息文本中搜索命中

# 回复类型常量（与 backend keyword_service.ALLOWED_REPLY_TYPES 一致）。
REPLY_TEXT: str = "text"  # 文本回复
REPLY_IMAGE: str = "image"  # 图片回复


# ----------------------------------------------------------------------
# 取值辅助：兼容「模型对象」与「dict」两种入参形态
# ----------------------------------------------------------------------
def _get_attr(item: Any, key: str, default: Any = None) -> Any:
    """从模型对象或字典中读取字段值，二者通用。

    上层可能传入 SQLAlchemy 模型实例（KeywordRule，属性访问）或普通 dict
    （键访问），此处统一兼容，保证本模块为纯逻辑且不绑定具体数据来源。

    Args:
        item: 模型对象或字典。
        key: 字段名 / 键名。
        default: 缺失时的默认值。

    Returns:
        字段值；缺失返回 default。
    """
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


# ----------------------------------------------------------------------
# 关键词命中结果（纯数据结构）
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class KeywordHit:
    """关键词规则命中结果。

    Attributes:
        rule_id: 命中规则的 ID（如入参提供）；否则为 None。
        keyword: 命中规则配置的关键词。
        match_type: 命中规则的匹配方式（full/contains/regex）。
        reply_type: 回复类型（text 文本 / image 图片，需求 6.5）。
        reply_content: 回复内容（文本或图片地址，需求 6.3）。
        priority: 命中规则的优先级（数值越大越优先）。
    """

    reply_type: str
    reply_content: str
    priority: int
    match_type: str
    keyword: str
    rule_id: Optional[int] = None


# ----------------------------------------------------------------------
# 单条规则命中判断
# ----------------------------------------------------------------------
def _match_single_rule(text: str, keyword: str, match_type: str) -> bool:
    """判断单条关键词规则是否命中当前消息文本（纯函数）。

    各匹配方式语义（与 backend 约定一致）：
    - full：消息文本与关键词去除两端空白后完全相等即命中；
    - contains：关键词为消息文本的子串即命中（空关键词不命中，避免误全量命中）；
    - regex：关键词作为正则在消息文本中搜索到即命中（非法正则视为不命中）；
    - 其它未知方式：一律不命中（向后兼容，避免误命中）。

    Args:
        text: 消息文本内容（None 已由上层归一为空串）。
        keyword: 关键词（匹配文本或正则表达式）。
        match_type: 匹配方式（full/contains/regex）。

    Returns:
        命中返回 True；否则 False。
    """
    if not match_type or keyword is None:
        return False

    if match_type == MATCH_FULL:
        # 全匹配：去除两端空白后完全相等。
        return text.strip() == keyword.strip()

    if match_type == MATCH_CONTAINS:
        # 包含匹配：空关键词不命中，避免「空串恒为子串」导致误全量命中。
        if keyword == "":
            return False
        return keyword in text

    if match_type == MATCH_REGEX:
        try:
            return re.search(keyword, text) is not None
        except re.error:
            # 非法正则：视为不命中，防止配置错误中断运行时会话。
            return False

    # 未知匹配方式：不命中。
    return False


# ----------------------------------------------------------------------
# 关键词规则集合匹配（需求 6.2 / 6.3 / 6.4 / 6.5 / 6.7）
# ----------------------------------------------------------------------
def match_keyword(
    text: Optional[str],
    rules: Optional[Iterable[Any]],
) -> Optional[KeywordHit]:
    """在关键词规则集合中匹配消息，返回优先级最高的唯一命中（需求 6.2-6.5）。

    匹配规则：
    - 仅启用规则（enabled=True）参与匹配，停用规则跳过（需求 6.7）；
    - 按各规则 match_type（full/contains/regex）判断是否命中消息文本；
    - 命中多条时仅返回 priority 最高的一条（需求 6.4）；priority 并列时取入参
      顺序靠前者，保证结果确定；
    - 命中结果原样携带 reply_type（text/image）与 reply_content，供上层发送
      文本或图片消息（需求 6.3 / 6.5）。

    Args:
        text: 客户消息文本内容（None 视为空串）。
        rules: 关键词规则集合（元素为 KeywordRule 模型对象或 dict，含 keyword /
            match_type / reply_type / reply_content / priority / enabled 字段）；
            None 视为空集合。

    Returns:
        命中返回优先级最高的 KeywordHit；无命中返回 None。
    """
    if not rules:
        return None

    safe_text = text or ""
    best: Optional[KeywordHit] = None

    for rule in rules:
        # 停用规则不参与匹配（需求 6.7）。
        if not bool(_get_attr(rule, "enabled", True)):
            continue

        keyword = _get_attr(rule, "keyword")
        match_type = _get_attr(rule, "match_type")
        if keyword is None or not match_type:
            continue

        if not _match_single_rule(safe_text, keyword, match_type):
            continue

        # 优先级：缺省视为 0；非整数兜底为 0，避免比较报错。
        try:
            priority = int(_get_attr(rule, "priority", 0) or 0)
        except (TypeError, ValueError):
            priority = 0

        # 仅当严格高于当前最优时替换，保证 priority 并列时取靠前者（确定性）。
        if best is not None and priority <= best.priority:
            continue

        best = KeywordHit(
            reply_type=_get_attr(rule, "reply_type", REPLY_TEXT) or REPLY_TEXT,
            reply_content=_get_attr(rule, "reply_content", "") or "",
            priority=priority,
            match_type=match_type,
            keyword=keyword,
            rule_id=_get_attr(rule, "id"),
        )

    return best


__all__ = [
    "MATCH_FULL",
    "MATCH_CONTAINS",
    "MATCH_REGEX",
    "REPLY_TEXT",
    "REPLY_IMAGE",
    "KeywordHit",
    "match_keyword",
]
