# -*- coding: utf-8 -*-
"""
websocket.engine.message_filter —— 消息过滤与黑名单判断（纯逻辑）
================================================================
本文件用途：实现 websocket 自动回复引擎运行时的「消息过滤规则命中判断」与
「黑名单判断」纯逻辑，供 reply_engine 决策链复用，满足需求 12：

- 需求 12.2：客户消息命中消息过滤规则时，引擎应跳过该消息的自动回复处理
  （上层据此记录消息日志为「已过滤」）。
- 需求 12.4：客户标识处于黑名单中时，引擎不对该客户消息触发自动回复。
- 需求 12.5（联动）：黑名单移出 = 逻辑失效（is_active=False），失效记录在
  运行时不再生效；本模块仅以 is_active=True 的记录参与判定。

设计要点（与 backend filter_service 保持一致）：
- 过滤条件类型枚举与后端一致：``contains``（包含）/``regex``（正则）/
  ``msg_type``（消息类型）；其余类型视为不命中（向后兼容，避免误拦截）。
- 停用规则（enabled=False）不参与命中判断；移出失效（is_active=False）的黑名单
  记录不参与黑名单判断（移出=失效，需求 12.5）。
- 纯逻辑、无 I/O：过滤规则与黑名单记录由上层传入（可为模型对象或 dict），
  本模块仅做命中判定，不访问数据库。

实现约束（开发规范）：
- 导入置顶（规范 51）；中文注释（规范 37）；单文件 ≤500 行（规范 35）；
  文件名用下划线（规范 40）；全中文（规范 50）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Optional

# 过滤条件类型常量（与 backend 数据字典 filter_condition 一致）。
CONDITION_CONTAINS: str = "contains"  # 包含匹配：条件值为消息文本子串
CONDITION_REGEX: str = "regex"  # 正则匹配：条件值为正则表达式
CONDITION_MSG_TYPE: str = "msg_type"  # 消息类型匹配：条件值等于消息类型


# ----------------------------------------------------------------------
# 取值辅助：兼容「模型对象」与「dict」两种入参形态
# ----------------------------------------------------------------------
def _get_attr(item: Any, key: str, default: Any = None) -> Any:
    """从模型对象或字典中读取字段值，二者通用。

    上层可能传入 SQLAlchemy 模型实例（属性访问）或普通 dict（键访问），此处
    统一兼容，保证本模块为纯逻辑且不绑定具体数据来源。

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
# 过滤命中结果（纯数据结构）
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class FilterHit:
    """过滤规则命中结果。

    Attributes:
        condition_type: 命中的过滤条件类型（contains/regex/msg_type）。
        condition_value: 命中的过滤条件值。
        rule_id: 命中规则的 ID（如入参提供）；否则为 None。
    """

    condition_type: str
    condition_value: str
    rule_id: Optional[int] = None


# ----------------------------------------------------------------------
# 单条过滤规则命中判断
# ----------------------------------------------------------------------
def _match_single_rule(
    text: str,
    msg_type: Optional[str],
    condition_type: str,
    condition_value: str,
) -> bool:
    """判断单条过滤规则是否命中当前消息（纯函数）。

    各条件类型语义（与 backend 约定一致）：
    - contains：condition_value 为 text 的子串即命中；
    - regex：condition_value 作为正则在 text 中搜索到即命中（非法正则视为不命中，
      避免运行时因配置错误而抛异常中断会话）；
    - msg_type：condition_value 等于消息类型 msg_type 即命中；
    - 其它未知类型：一律不命中（向后兼容，避免误拦截）。

    Args:
        text: 消息文本内容（None 视为空串）。
        msg_type: 消息类型（如 text/image/video 等；可为 None）。
        condition_type: 过滤条件类型。
        condition_value: 过滤条件值。

    Returns:
        命中返回 True；否则 False。
    """
    if not condition_type or condition_value is None:
        return False

    safe_text = text or ""

    if condition_type == CONDITION_CONTAINS:
        # 包含匹配：空条件值不命中，避免「空串恒为子串」导致全量误拦截。
        if condition_value == "":
            return False
        return condition_value in safe_text

    if condition_type == CONDITION_REGEX:
        try:
            return re.search(condition_value, safe_text) is not None
        except re.error:
            # 非法正则：视为不命中，防止配置错误中断运行时会话。
            return False

    if condition_type == CONDITION_MSG_TYPE:
        return msg_type is not None and condition_value == msg_type

    # 未知条件类型：不命中。
    return False


# ----------------------------------------------------------------------
# 过滤规则集合命中判断（需求 12.2）
# ----------------------------------------------------------------------
def match_filter_rules(
    text: Optional[str],
    rules: Optional[Iterable[Any]],
    *,
    msg_type: Optional[str] = None,
) -> Optional[FilterHit]:
    """判断消息是否命中任一启用的过滤规则（需求 12.2）。

    仅启用（enabled=True）的规则参与判断；按入参顺序返回首个命中的规则结果。
    上层据返回值决定是否跳过自动回复并记录消息日志为「已过滤」。

    Args:
        text: 消息文本内容。
        rules: 过滤规则集合（元素为模型对象或 dict，含 condition_type /
            condition_value / enabled 字段）；None 视为空集合。
        msg_type: 消息类型（用于 msg_type 条件判断）。

    Returns:
        命中返回 FilterHit；未命中返回 None。
    """
    if not rules:
        return None

    for rule in rules:
        # 停用规则不参与命中判断（与 backend enabled 约定一致）。
        if not bool(_get_attr(rule, "enabled", True)):
            continue
        condition_type = _get_attr(rule, "condition_type")
        condition_value = _get_attr(rule, "condition_value")
        if _match_single_rule(text or "", msg_type, condition_type, condition_value):
            return FilterHit(
                condition_type=condition_type,
                condition_value=condition_value,
                rule_id=_get_attr(rule, "id"),
            )
    return None


def is_filtered(
    text: Optional[str],
    rules: Optional[Iterable[Any]],
    *,
    msg_type: Optional[str] = None,
) -> bool:
    """消息是否被过滤（命中任一启用过滤规则即为 True，需求 12.2）。

    Args:
        text: 消息文本内容。
        rules: 过滤规则集合。
        msg_type: 消息类型。

    Returns:
        命中返回 True；否则 False。
    """
    return match_filter_rules(text, rules, msg_type=msg_type) is not None


# ----------------------------------------------------------------------
# 黑名单判断（需求 12.4 / 12.5）
# ----------------------------------------------------------------------
def is_blacklisted(
    customer_uid: Optional[str],
    blacklist: Optional[Iterable[Any]],
) -> bool:
    """判断客户标识是否处于「有效」黑名单中（需求 12.4 / 12.5）。

    仅 is_active=True 的黑名单记录生效；移出失效（is_active=False）的记录不参与
    判断（移出=失效，逻辑删除，需求 12.5）。客户标识为空一律视为不在黑名单。

    Args:
        customer_uid: 客户唯一标识。
        blacklist: 黑名单记录集合（元素为模型对象或 dict，含 customer_uid /
            is_active 字段）；None 视为空集合。

    Returns:
        命中有效黑名单记录返回 True；否则 False。
    """
    if not customer_uid or not blacklist:
        return False

    for item in blacklist:
        # 移出失效记录不生效（需求 12.5）。
        if not bool(_get_attr(item, "is_active", True)):
            continue
        if _get_attr(item, "customer_uid") == customer_uid:
            return True
    return False


__all__ = [
    "CONDITION_CONTAINS",
    "CONDITION_REGEX",
    "CONDITION_MSG_TYPE",
    "FilterHit",
    "match_filter_rules",
    "is_filtered",
    "is_blacklisted",
]
