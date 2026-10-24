# -*- coding: utf-8 -*-
"""
websocket.agent.goods_card_fallback —— 商品卡片签名缺失降级为文本回复（纯逻辑）
============================================================================
本文件用途：实现自动回复引擎 / AI 回复引擎在「需要向客户发送商品卡片，但
``anti-content`` 风控签名不可用」时的降级处理，满足需求 26：

- 需求 26.3：商品卡片发送因签名问题暂不可用时，自动回复引擎与 AI 回复引擎应
  改为以文本方式回复商品信息（降级），而非中断会话处理。
- 需求 26.4：文本自动回复、关键词回复、AI 文本回复、转人工等不依赖签名的能力，
  在签名能力暂不可用时仍应正常运作（本模块仅对商品卡片这一签名依赖能力降级，
  不影响其它能力的产出）。
- 需求 26.5：因外部依赖暂不可用而降级某能力时，应通过统一响应体或消息日志向
  用户呈现具体的「中文降级原因」。
- 需求 26.6（联动）：检测到 Cookie 缺少有效签名时，提示可通过「账号密码登录」
  重新获取含完整签名的 Cookie 以恢复相关能力（复用 SIGNATURE_MISSING_MESSAGE）。

设计要点（纯逻辑、无 I/O，便于单元 / 属性测试）：
- 复用任务 10.1 的 ``channel_pdd.core.anti_content`` 签名缺失 / 失效检测
  （``has_valid_anti_content`` / ``is_signature_invalid_response``），不重复实现
  签名判定逻辑（开发规范 36 / 52）。
- ``build_goods_text``：将商品上下文（名称 / 价格 / 规格 / goods_id 等）格式化为
  中文文本，作为降级回复内容。
- ``resolve_goods_card_reply``：判定签名是否可用；可用时输出「商品卡片」决策，
  不可用时降级为「文本」决策并携带中文降级原因，且始终 ``interrupted=False``
  （不中断会话，需求 26.3）。

实现约束（开发规范）：
- 导入置顶（规范 51）；中文注释（规范 37）；单文件 ≤500 行（规范 35）；
  文件名用下划线（规范 40）；全中文（规范 50）；复用共通签名检测（规范 52）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional

from channel_pdd.core.anti_content import (
    SIGNATURE_MISSING_MESSAGE,
    has_valid_anti_content,
    is_signature_invalid_response,
)

# 降级回复类型常量（与关键词匹配器 / 消息发送口径一致）。
REPLY_GOODS_CARD: str = "goods_card"  # 正常：发送商品卡片
REPLY_TEXT: str = "text"  # 降级：以文本回复商品信息

# 商品上下文中可能出现的字段键名（兼容消息解析 goods_context 的命名）。
_GOODS_NAME_KEYS: tuple[str, ...] = ("goods_name", "goodsName", "name")
_GOODS_PRICE_KEYS: tuple[str, ...] = ("goods_price", "goodsPrice", "price")
_GOODS_SPEC_KEYS: tuple[str, ...] = ("goods_spec", "spec")
_GOODS_ID_KEYS: tuple[str, ...] = ("goods_id", "goodsID", "goodsId")
_GOODS_LINK_KEYS: tuple[str, ...] = ("link_url", "linkUrl", "url")

# 降级原因模板（中文，需求 26.5）：在签名缺失提示后追加「已降级为文本回复」。
DEGRADE_REASON_TEMPLATE: str = "{cause}；已降级为文本回复商品信息"


@dataclass(frozen=True)
class GoodsCardFallbackResult:
    """商品卡片降级判定结果（纯数据结构）。

    Attributes:
        degraded: 是否已降级为文本回复（True=签名不可用、已降级；False=可正常发卡）。
        reply_type: 回复类型（``goods_card`` 正常发卡 / ``text`` 降级文本）。
        goods_id: 目标商品 ID（如可提取）；缺失为 None。
        text_content: 降级文本内容（含商品信息，仅降级时非空）；正常发卡为 None。
        degrade_reason: 中文降级原因（仅降级时非空，需求 26.5）；正常发卡为 None。
        interrupted: 是否中断会话；按需求 26.3 恒为 False（降级而非中断）。
    """

    degraded: bool
    reply_type: str
    goods_id: Optional[str] = None
    text_content: Optional[str] = None
    degrade_reason: Optional[str] = None
    interrupted: bool = False


def _pick(goods_info: Optional[Mapping[str, Any]], keys: tuple[str, ...]) -> Optional[str]:
    """从商品上下文按候选键名顺序提取首个非空值并转为去空白字符串。

    Args:
        goods_info: 商品上下文字典（兼容多种字段命名）；非映射返回 None。
        keys: 候选键名（按优先级顺序）。

    Returns:
        提取到的非空字符串（已去首尾空白）；均缺失或为空时返回 None。
    """
    if not isinstance(goods_info, Mapping):
        return None
    for key in keys:
        value = goods_info.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def build_goods_text(goods_info: Optional[Mapping[str, Any]]) -> str:
    """将商品上下文格式化为中文文本，作为降级回复内容（需求 26.3）。

    依次拼接「商品名称 / 价格 / 规格 / 商品编号 / 链接」中存在的字段；当无任何
    可用字段时，回退为通用提示文本，确保降级回复始终有内容、不中断会话。

    Args:
        goods_info: 商品上下文字典（名称 / 价格 / 规格 / goods_id 等）。

    Returns:
        面向客户的中文商品信息文本。
    """
    name = _pick(goods_info, _GOODS_NAME_KEYS)
    price = _pick(goods_info, _GOODS_PRICE_KEYS)
    spec = _pick(goods_info, _GOODS_SPEC_KEYS)
    goods_id = _pick(goods_info, _GOODS_ID_KEYS)
    link = _pick(goods_info, _GOODS_LINK_KEYS)

    lines: list[str] = []
    if name:
        lines.append(f"商品名称：{name}")
    if price:
        lines.append(f"商品价格：{price}")
    if spec:
        lines.append(f"商品规格：{spec}")
    if goods_id:
        lines.append(f"商品编号：{goods_id}")
    if link:
        lines.append(f"商品链接：{link}")

    if not lines:
        # 无任何商品字段：给出通用兜底文本，保证降级回复非空（需求 26.3）。
        return "为您推荐相关商品，详情请咨询客服。"

    header = "为您提供以下商品信息："
    return header + "\n" + "\n".join(lines)


def is_signature_available(
    cookies: Optional[Mapping[str, Any]] = None,
    response_data: Optional[Mapping[str, Any]] = None,
) -> bool:
    """判定商品卡片所需的 anti-content 签名当前是否可用（复用任务 10.1 检测）。

    判定规则（任一条件不满足即视为签名不可用）：
    - Cookie 必须携带非空 anti-content 签名（``has_valid_anti_content``）；
    - 若提供了接口响应体，且其被识别为「签名校验失败 / 风控拦截」
      （``is_signature_invalid_response``），同样视为不可用。

    Args:
        cookies: 当前店铺 Cookie 字典（含 anti-content）。
        response_data: 商品卡片接口的响应体（可选，用于识别返回的签名失败）。

    Returns:
        签名可用返回 True；缺失 / 失效返回 False。
    """
    if not has_valid_anti_content(cookies):
        return False
    if response_data is not None and is_signature_invalid_response(response_data):
        return False
    return True


def resolve_goods_card_reply(
    goods_info: Optional[Mapping[str, Any]],
    *,
    cookies: Optional[Mapping[str, Any]] = None,
    response_data: Optional[Mapping[str, Any]] = None,
    cause_message: Optional[str] = None,
) -> GoodsCardFallbackResult:
    """决策商品卡片回复：签名可用则发卡，不可用则降级为文本（需求 26.3/26.5）。

    - 当签名可用时，返回 ``reply_type=goods_card`` 的正常决策（不降级）；
    - 当签名缺失 / 失效时，返回 ``reply_type=text`` 的降级决策：以
      ``build_goods_text`` 生成的中文商品信息文本作为回复内容，并携带中文降级
      原因（``cause_message`` 缺省采用统一签名缺失提示 SIGNATURE_MISSING_MESSAGE，
      引导用户通过「账号密码登录」恢复，需求 26.6）；
    - 无论是否降级，``interrupted`` 恒为 False，即不中断会话处理（需求 26.3）。

    Args:
        goods_info: 商品上下文（名称 / 价格 / 规格 / goods_id 等）。
        cookies: 当前店铺 Cookie 字典（含 anti-content）。
        response_data: 商品卡片接口响应体（可选，用于识别返回的签名失败）。
        cause_message: 自定义中文降级原因前缀；缺省采用签名缺失统一提示。

    Returns:
        商品卡片降级判定结果 GoodsCardFallbackResult。
    """
    goods_id = _pick(goods_info, _GOODS_ID_KEYS)

    # 签名可用：正常发送商品卡片，不降级。
    if is_signature_available(cookies=cookies, response_data=response_data):
        return GoodsCardFallbackResult(
            degraded=False,
            reply_type=REPLY_GOODS_CARD,
            goods_id=goods_id,
            text_content=None,
            degrade_reason=None,
            interrupted=False,
        )

    # 签名不可用：降级为文本回复商品信息，携带中文降级原因（需求 26.5）。
    cause = (cause_message or "").strip() or SIGNATURE_MISSING_MESSAGE
    degrade_reason = DEGRADE_REASON_TEMPLATE.format(cause=cause)
    return GoodsCardFallbackResult(
        degraded=True,
        reply_type=REPLY_TEXT,
        goods_id=goods_id,
        text_content=build_goods_text(goods_info),
        degrade_reason=degrade_reason,
        interrupted=False,
    )


__all__ = [
    "REPLY_GOODS_CARD",
    "REPLY_TEXT",
    "DEGRADE_REASON_TEMPLATE",
    "GoodsCardFallbackResult",
    "build_goods_text",
    "is_signature_available",
    "resolve_goods_card_reply",
]
