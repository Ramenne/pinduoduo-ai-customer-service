# -*- coding: utf-8 -*-
"""
backend.app.services.keyword_service —— 关键词规则管理业务服务
==============================================================
本文件用途：实现 backend 服务的「关键词自动回复规则」业务逻辑，供 keywords
路由复用，满足需求 6（关键词自动回复）中的「配置管理」部分：

- ``create_keyword_rule(...)``：创建关键词规则（指定关键词、匹配方式、回复类型、
  回复内容、优先级），持久化并返回脱敏统一响应体（需求 6.1）。
- ``update_keyword_rule(...)``：修改关键词规则字段（需求 6.1 配套），变更在
  下一条消息处理时由 websocket 引擎读取最新规则生效。
- ``list_keyword_rules(...)``：关键词规则列表后端分页查询，可按店铺、启用状态
  筛选（需求 6.6，后端分页）。
- ``get_keyword_rule(...)``：查询单条关键词规则。
- ``set_keyword_rule_status(...)``：启用 / 停用关键词规则（需求 6.7）。停用经
  ``enabled=False`` 标记，停用规则不参与匹配；该变更「在下一条消息处理时生效」
  ——websocket 自动回复引擎在每次处理消息时按 ``enabled`` 实时读取规则集，无需
  额外刷新。
- ``delete_keyword_rule(...)``：删除关键词规则。依据《开发规范》第 11 条「禁止
  物理删除业务数据」，此处为逻辑删除——通过状态字段 ``enabled=False`` 标记失效，
  记录保留、总数不变（与停用共用 ``enabled`` 字段）。

实现约束（开发规范）：
- 统一响应体由 common.schemas.common 构造，HTTP 恒 200（规范 1-3）。
- 所有数据访问经 common.db.repository 的参数化查询，禁止拼接 SQL（规范 16）。
- 匹配方式 / 回复类型为枚举键，与 sys_dict 登记一致（match_type:
  full/contains/regex；reply_type: text/image，需求 24.7）；本服务做合法性校验。
- 禁止物理删除业务数据，删除 / 停用经状态字段实现（规范 11 / 需求 24.6）。
- 导入置顶（规范 51）；中文注释（规范 37）；单文件 ≤500 行（规范 35）。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.core.business_codes import CODE_NOT_FOUND, CODE_PARAM_ERROR
from app.core.data_scope import ensure_shop_in_scope, paginate_shop_scoped
from common.db.repository import Repository
from common.models.reply_models import KeywordRule
from common.schemas.common import ApiResponse, error_response, success_response

# 关键词规则启停用状态（与 KeywordRule.enabled 约定一致）。
RULE_ENABLED: bool = True
RULE_DISABLED: bool = False

# 合法的匹配方式枚举键（与 sys_dict 的 match_type 字典一致，需求 6.x / 24.7）。
ALLOWED_MATCH_TYPES: tuple[str, ...] = ("full", "contains", "regex")

# 合法的回复类型枚举键（与 sys_dict 的 reply_type 字典一致，需求 7.x / 24.7）。
ALLOWED_REPLY_TYPES: tuple[str, ...] = ("text", "image")


# ----------------------------------------------------------------------
# 序列化辅助
# ----------------------------------------------------------------------
def serialize_keyword_rule(rule: KeywordRule) -> Dict[str, Any]:
    """将关键词规则模型序列化为对外字典。

    关键词规则不含敏感字段，直接输出展示所需字段；时间字段为北京时间。

    Args:
        rule: 关键词规则模型实例。

    Returns:
        关键词规则信息字典。
    """
    return {
        "id": rule.id,
        "shop_pk": rule.shop_pk,
        "keyword": rule.keyword,
        "match_type": rule.match_type,
        "reply_type": rule.reply_type,
        "reply_content": rule.reply_content,
        "priority": rule.priority,
        "enabled": bool(rule.enabled),
        "created_at": rule.created_at,
        "updated_at": rule.updated_at,
    }


# ----------------------------------------------------------------------
# 入参校验辅助
# ----------------------------------------------------------------------
def _validate_rule_fields(
    keyword: Optional[str],
    match_type: Optional[str],
    reply_type: Optional[str],
    reply_content: Optional[str],
    *,
    require_all: bool = True,
) -> Optional[ApiResponse]:
    """校验关键词规则核心字段，非法时返回失败响应，合法返回 None。

    校验项（需求 6.1）：关键词非空；匹配方式属于合法枚举；回复类型属于合法
    枚举；回复内容非空；当匹配方式为正则时正则可编译。``require_all=False``
    时（用于部分更新），仅对「显式提供（非 None）」的字段做校验。

    Args:
        keyword: 关键词（匹配文本或正则）。
        match_type: 匹配方式（full/contains/regex）。
        reply_type: 回复类型（text/image）。
        reply_content: 回复内容（文本或图片地址）。
        require_all: 是否要求全部字段必填（创建时为 True，更新时为 False）。

    Returns:
        校验不通过时返回失败统一响应体；通过返回 None。
    """
    # 关键词：必填或（更新时）显式提供时不可为空白。
    if keyword is not None:
        if not keyword.strip():
            return error_response(CODE_PARAM_ERROR, "关键词不能为空")
    elif require_all:
        return error_response(CODE_PARAM_ERROR, "关键词不能为空")

    # 匹配方式：必填或显式提供时必须为合法枚举。
    if match_type is not None:
        if match_type not in ALLOWED_MATCH_TYPES:
            return error_response(
                CODE_PARAM_ERROR,
                f"匹配方式非法，可选值为 {list(ALLOWED_MATCH_TYPES)}",
            )
    elif require_all:
        return error_response(CODE_PARAM_ERROR, "匹配方式不能为空")

    # 回复类型：必填或显式提供时必须为合法枚举。
    if reply_type is not None:
        if reply_type not in ALLOWED_REPLY_TYPES:
            return error_response(
                CODE_PARAM_ERROR,
                f"回复类型非法，可选值为 {list(ALLOWED_REPLY_TYPES)}",
            )
    elif require_all:
        return error_response(CODE_PARAM_ERROR, "回复类型不能为空")

    # 回复内容：必填或显式提供时不可为空白。
    if reply_content is not None:
        if not reply_content.strip():
            return error_response(CODE_PARAM_ERROR, "回复内容不能为空")
    elif require_all:
        return error_response(CODE_PARAM_ERROR, "回复内容不能为空")

    # 正则匹配方式：校验关键词为可编译的正则，避免运行时匹配报错。
    if match_type == "regex" and keyword is not None and keyword.strip():
        try:
            re.compile(keyword)
        except re.error:
            return error_response(CODE_PARAM_ERROR, "正则表达式格式非法")

    return None


# ----------------------------------------------------------------------
# 创建（需求 6.1）
# ----------------------------------------------------------------------
def create_keyword_rule(
    session: Session,
    shop_pk: int,
    keyword: str,
    match_type: str,
    reply_content: str,
    *,
    reply_type: str = "text",
    priority: int = 0,
    enabled: bool = True,
    operator_id: Optional[int] = None,
) -> ApiResponse:
    """创建关键词规则并持久化（需求 6.1）。

    Args:
        session: 数据库会话。
        shop_pk: 关联店铺主键 shop.id。
        keyword: 关键词（匹配文本或正则）。
        match_type: 匹配方式（full/contains/regex）。
        reply_content: 回复内容（文本或图片地址）。
        reply_type: 回复类型（text/image），默认 text。
        priority: 优先级（越大越优先），默认 0。
        enabled: 是否启用，默认 True。
        operator_id: 操作人用户 ID，作为创建人审计字段。

    Returns:
        统一响应体：成功返回 data=规则信息；失败返回对应中文提示。
    """
    # 店铺主键校验：必须为正整数（普通列，无外键，关系由代码维护）。
    if shop_pk is None or int(shop_pk) <= 0:
        return error_response(CODE_PARAM_ERROR, "店铺标识非法")

    # 数据范围隔离：店铺需存在且在当前用户可见范围内（需求 3.7 / 规范 42a）。
    denied = ensure_shop_in_scope(session, int(shop_pk), operator_id)
    if denied is not None:
        return denied

    # 核心字段合法性校验（创建时全部必填）。
    denied = _validate_rule_fields(
        keyword, match_type, reply_type, reply_content, require_all=True
    )
    if denied is not None:
        return denied

    rule = Repository(KeywordRule, session).create(
        shop_pk=int(shop_pk),
        keyword=keyword.strip(),
        match_type=match_type,
        reply_type=reply_type,
        reply_content=reply_content,
        priority=int(priority) if priority is not None else 0,
        enabled=bool(enabled),
        created_by=operator_id,
    )
    return success_response(
        data=serialize_keyword_rule(rule),
        message="创建成功",
    )


# ----------------------------------------------------------------------
# 修改（需求 6.1 配套）
# ----------------------------------------------------------------------
def update_keyword_rule(
    session: Session,
    rule_id: int,
    *,
    keyword: Optional[str] = None,
    match_type: Optional[str] = None,
    reply_type: Optional[str] = None,
    reply_content: Optional[str] = None,
    priority: Optional[int] = None,
    operator_id: Optional[int] = None,
) -> ApiResponse:
    """修改关键词规则字段（需求 6.1 配套）。

    仅更新显式提供（非 None）的字段；变更在下一条消息处理时由 websocket 引擎
    读取最新规则生效。非管理员仅可操作其可见范围内店铺的规则（需求 3.7）。

    Args:
        session: 数据库会话。
        rule_id: 目标规则 ID。
        keyword: 新关键词；None 表示不修改。
        match_type: 新匹配方式；None 表示不修改。
        reply_type: 新回复类型；None 表示不修改。
        reply_content: 新回复内容；None 表示不修改。
        priority: 新优先级；None 表示不修改。
        operator_id: 当前操作用户 ID（数据范围校验）。

    Returns:
        统一响应体：成功返回更新后的规则信息。
    """
    rule_repo = Repository(KeywordRule, session)
    rule = rule_repo.get(rule_id)
    if rule is None:
        return error_response(CODE_NOT_FOUND, "目标关键词规则不存在")

    # 数据范围隔离：规则所属店铺需在当前用户可见范围内（需求 3.7 / 规范 42a）。
    denied = ensure_shop_in_scope(session, rule.shop_pk, operator_id)
    if denied is not None:
        return denied

    # 对显式提供的字段做合法性校验（部分更新）。需结合「最终生效的匹配方式」
    # 校验正则，故合并当前值与新值后再校验。
    effective_match_type = match_type if match_type is not None else rule.match_type
    effective_keyword = keyword if keyword is not None else rule.keyword
    denied = _validate_rule_fields(
        effective_keyword if keyword is not None or match_type == "regex" else None,
        match_type,
        reply_type,
        reply_content,
        require_all=False,
    )
    if denied is not None:
        return denied
    # 若最终匹配方式为正则，额外确保关键词为可编译正则。
    if effective_match_type == "regex":
        try:
            re.compile(effective_keyword)
        except re.error:
            return error_response(CODE_PARAM_ERROR, "正则表达式格式非法")

    # 组装待更新字段（仅显式提供的字段）。
    values: Dict[str, Any] = {}
    if keyword is not None:
        values["keyword"] = keyword.strip()
    if match_type is not None:
        values["match_type"] = match_type
    if reply_type is not None:
        values["reply_type"] = reply_type
    if reply_content is not None:
        values["reply_content"] = reply_content.strip()
    if priority is not None:
        values["priority"] = int(priority)

    if not values:
        return error_response(CODE_PARAM_ERROR, "未提供任何待更新字段")

    rule_repo.update(rule_id, **values)
    return success_response(
        data=serialize_keyword_rule(rule),
        message="更新成功",
    )


# ----------------------------------------------------------------------
# 启停用（需求 6.7：下一条消息生效）
# ----------------------------------------------------------------------
def set_keyword_rule_status(
    session: Session,
    rule_id: int,
    enabled: bool,
    *,
    operator_id: Optional[int] = None,
) -> ApiResponse:
    """启用或停用关键词规则（需求 6.7）。

    停用经状态字段 ``enabled=False`` 标记，停用规则不参与匹配。该变更「在下一条
    消息处理时生效」——websocket 自动回复引擎每次处理消息时按 ``enabled`` 实时读取
    规则集，故无需额外刷新机制。非管理员仅可操作其可见范围内店铺的规则（需求 3.7）。

    Args:
        session: 数据库会话。
        rule_id: 目标规则 ID。
        enabled: True=启用，False=停用。
        operator_id: 当前操作用户 ID（数据范围校验）。

    Returns:
        统一响应体：成功返回更新后的规则信息。
    """
    rule_repo = Repository(KeywordRule, session)
    rule = rule_repo.get(rule_id)
    if rule is None:
        return error_response(CODE_NOT_FOUND, "目标关键词规则不存在")

    # 数据范围隔离：规则所属店铺需在当前用户可见范围内（需求 3.7 / 规范 42a）。
    denied = ensure_shop_in_scope(session, rule.shop_pk, operator_id)
    if denied is not None:
        return denied

    rule_repo.update(rule_id, enabled=bool(enabled))
    message = "已启用" if enabled else "已停用"
    return success_response(
        data=serialize_keyword_rule(rule),
        message=message,
    )


# ----------------------------------------------------------------------
# 删除（逻辑删除：禁止物理删除业务数据，规范 11 / 需求 24.6）
# ----------------------------------------------------------------------
def delete_keyword_rule(
    session: Session, rule_id: int, *, operator_id: Optional[int] = None
) -> ApiResponse:
    """逻辑删除关键词规则（需求 6.1 配套，规范 11）。

    依据「禁止物理删除业务数据」，此处通过状态字段 ``enabled=False`` 标记失效，
    记录保留、总数不变。非管理员仅可操作其可见范围内店铺的规则（需求 3.7）。

    Args:
        session: 数据库会话。
        rule_id: 目标规则 ID。
        operator_id: 当前操作用户 ID（数据范围校验）。

    Returns:
        统一响应体：成功返回提示；不存在返回失败。
    """
    rule_repo = Repository(KeywordRule, session)
    rule = rule_repo.get(rule_id)
    if rule is None:
        return error_response(CODE_NOT_FOUND, "目标关键词规则不存在")

    # 数据范围隔离：规则所属店铺需在当前用户可见范围内（需求 3.7 / 规范 42a）。
    denied = ensure_shop_in_scope(session, rule.shop_pk, operator_id)
    if denied is not None:
        return denied

    # 经仓储层逻辑删除（自动探测 enabled 状态字段置为失效值），禁止物理删除。
    rule_repo.soft_delete(rule_id)
    return success_response(data=None, message="已删除")


# ----------------------------------------------------------------------
# 查询单条
# ----------------------------------------------------------------------
def get_keyword_rule(
    session: Session, rule_id: int, *, operator_id: Optional[int] = None
) -> ApiResponse:
    """查询单条关键词规则。

    非管理员仅可查看其可见范围内店铺的规则（数据范围隔离，需求 3.7）。

    Args:
        session: 数据库会话。
        rule_id: 规则 ID。
        operator_id: 当前操作用户 ID（数据范围校验）。

    Returns:
        统一响应体：成功返回规则信息；不存在返回失败。
    """
    rule = Repository(KeywordRule, session).get(rule_id)
    if rule is None:
        return error_response(CODE_NOT_FOUND, "目标关键词规则不存在")

    # 数据范围隔离：规则所属店铺需在当前用户可见范围内（需求 3.7 / 规范 42a）。
    denied = ensure_shop_in_scope(session, rule.shop_pk, operator_id)
    if denied is not None:
        return denied

    return success_response(
        data=serialize_keyword_rule(rule),
        message="查询成功",
    )


# ----------------------------------------------------------------------
# 列表（后端分页，需求 6.6）
# ----------------------------------------------------------------------
def list_keyword_rules(
    session: Session,
    page: Any = 1,
    page_size: Any = 20,
    shop_pk: Optional[int] = None,
    enabled: Optional[bool] = None,
    *,
    operator_id: Optional[int] = None,
) -> ApiResponse:
    """分页查询关键词规则列表（需求 6.6，后端分页）。

    非管理员仅可见其可访问店铺下的规则（数据范围隔离，需求 3.7）。默认按创建
    时间倒序（仓储层自动探测时间字段）。可按店铺与启用状态筛选。

    Args:
        session: 数据库会话。
        page: 页码（从 1 开始，将被规整）。
        page_size: 每页条数（10/20/50/100，将被规整）。
        shop_pk: 按店铺主键筛选；None 表示该用户可见范围内全部店铺。
        enabled: 按启用状态筛选；None 表示不筛选。
        operator_id: 当前操作用户 ID（数据范围校验）。

    Returns:
        统一响应体：data 为分页结构 {list, total, page, page_size}。
    """
    return paginate_shop_scoped(
        session,
        model=KeywordRule,
        serializer=serialize_keyword_rule,
        page=page,
        page_size=page_size,
        operator_id=operator_id,
        shop_pk=int(shop_pk) if shop_pk is not None else None,
        extra_filters={"enabled": bool(enabled)} if enabled is not None else None,
    )


__all__ = [
    "RULE_ENABLED",
    "RULE_DISABLED",
    "ALLOWED_MATCH_TYPES",
    "ALLOWED_REPLY_TYPES",
    "serialize_keyword_rule",
    "create_keyword_rule",
    "update_keyword_rule",
    "set_keyword_rule_status",
    "delete_keyword_rule",
    "get_keyword_rule",
    "list_keyword_rules",
]
