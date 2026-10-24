# -*- coding: utf-8 -*-
"""
common.services.kb_service —— 知识库检索服务（kb.search）
==========================================================
本文件用途：实现「拼多多自动回复」系统的知识库检索逻辑 ``kb.search``，置于
common 公共库供 websocket 服务（AI 回复引擎工具 get_product_knowledge /
search_customer_service_knowledge）复用（设计「关键接口签名」：
``kb.search(shop_id, query?, goods_id?, limit) -> {product_knowledge,
customer_service_knowledge}``）。

检索约束（需求 9.4 / 10.3 / 10.4 / 10.5）：
- 限定店铺：仅检索给定店铺（shop_pk）下的知识，跨店铺数据不返回（数据隔离）；
- 客服知识仅启用：仅返回 ``enabled=True`` 的客服知识，停用记录检索时不返回
  （需求 10.5）；商品知识同理仅返回 ``status=1`` 的启用记录（停用=逻辑删除）；
- 结果不超 limit：商品知识与客服知识各自返回数量均不超过 ``limit``（需求 10.3
  的「结果数量不超过配置上限」）；
- 按 goods_id 精确匹配：提供 ``goods_id`` 时商品知识按其精确匹配（需求 9.4）；
- 按标签筛选：提供 ``tags`` 时客服知识按标签（逗号分隔）筛选（需求 10.4）；
- jieba 分词：提供 ``query`` 时对其中文分词，命中标题 / 内容 / 标签任一分词
  的启用客服知识方为匹配，并按命中分词数量降序排序（需求 10.3）。

设计说明：
- 所有数据访问经 ``common.db.repository.Repository``（SQLAlchemy 参数化查询，
  规范 16），本文件不书写任何原生 SQL；
- 「店铺 + 启用 + goods_id 精确」等等值条件下推数据库过滤；分词命中与标签
  筛选在内存中对「本店铺启用集合」二次过滤（单店铺知识量有限，开销可控）；
- 参数 ``shop_pk`` 对应设计签名中的 ``shop_id``，即店铺主键 shop.id（知识表
  以 ``shop_pk`` 列关联店铺，无外键，规范 10）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import jieba
from sqlalchemy.orm import Session

from common.db.repository import Repository
from common.models.knowledge_models import CustomerServiceKnowledge, ProductKnowledge

# 检索结果默认上限：未显式传入 limit 时使用（需求 10.3 配置上限的兜底默认）。
DEFAULT_SEARCH_LIMIT: int = 10


@dataclass
class KbSearchResult:
    """知识检索结果数据结构。

    对应设计签名返回值 ``{product_knowledge, customer_service_knowledge}``：
    - ``product_knowledge``：命中的商品知识记录列表（启用，按 goods_id 精确匹配）；
    - ``customer_service_knowledge``：命中的客服知识记录列表（仅启用，
      经分词 / 标签筛选，按相关度排序）。
    两个列表长度均不超过检索 ``limit``。
    """

    product_knowledge: list[ProductKnowledge] = field(default_factory=list)
    customer_service_knowledge: list[CustomerServiceKnowledge] = field(
        default_factory=list
    )


def _normalize_limit(limit: int | None) -> int:
    """规整检索条数上限为正整数；非法 / 缺省时回退默认值。

    Args:
        limit: 调用方传入的结果上限（可能为 None 或非正数）。

    Returns:
        合法的正整数上限。
    """
    if limit is None:
        return DEFAULT_SEARCH_LIMIT
    try:
        value = int(limit)
    except (TypeError, ValueError):
        return DEFAULT_SEARCH_LIMIT
    return value if value > 0 else DEFAULT_SEARCH_LIMIT


def _tokenize(query: str) -> list[str]:
    """对查询文本进行 jieba 中文分词，返回去重去空白的分词列表。

    Args:
        query: 原始查询文本。

    Returns:
        小写化、去空白、去重后的分词列表（保持首次出现顺序）；空查询返回空列表。
    """
    if not query or not query.strip():
        return []
    tokens: list[str] = []
    seen: set[str] = set()
    # jieba.lcut 返回分词列表；过滤纯空白分词，统一小写以便不区分大小写匹配
    for raw in jieba.lcut(query):
        token = raw.strip().lower()
        if token and token not in seen:
            seen.add(token)
            tokens.append(token)
    return tokens


def _split_tags(tags: str | None) -> list[str]:
    """将逗号分隔的标签字符串拆分为去空白、非空的标签列表（小写）。

    Args:
        tags: 逗号分隔的标签字符串（可能为 None / 空）。

    Returns:
        标签列表；无有效标签返回空列表。
    """
    if not tags:
        return []
    return [t.strip().lower() for t in tags.split(",") if t.strip()]


def _match_token_count(record: CustomerServiceKnowledge, tokens: list[str]) -> int:
    """统计客服知识记录命中的分词数量（标题 / 内容 / 标签任一包含即计 1）。

    Args:
        record: 客服知识记录。
        tokens: 查询分词列表（已小写）。

    Returns:
        命中的分词数量（用于相关度排序）。
    """
    # 拼接可检索文本（标题 + 内容 + 标签），统一小写后做包含匹配
    haystack = " ".join(
        part.lower()
        for part in (record.title, record.content, record.tags)
        if part
    )
    return sum(1 for token in tokens if token in haystack)


def _filter_by_tags(
    record: CustomerServiceKnowledge, want_tags: list[str]
) -> bool:
    """判断客服知识记录是否命中任一指定标签（需求 10.4）。

    Args:
        record: 客服知识记录。
        want_tags: 期望筛选的标签列表（已小写）。

    Returns:
        记录标签与期望标签存在交集时返回 True。
    """
    record_tags = set(_split_tags(record.tags))
    return any(tag in record_tags for tag in want_tags)


def search(
    session: Session,
    shop_pk: int,
    query: str | None = None,
    goods_id: str | None = None,
    limit: int | None = DEFAULT_SEARCH_LIMIT,
    tags: list[str] | str | None = None,
) -> KbSearchResult:
    """知识库检索：限定店铺，返回商品知识与客服知识匹配结果。

    对应设计签名 ``kb.search(shop_id, query?, goods_id?, limit)``，供 AI 回复
    引擎的工具调用（商品知识 / 客服知识检索）复用。检索严格满足：
    限定店铺、客服知识仅启用、结果不超 limit、按 goods_id 精确匹配商品知识、
    按标签筛选客服知识、对 query 进行 jieba 分词匹配（需求 9.4 / 10.3 / 10.4 /
    10.5）。

    Args:
        session: 当前事务性会话（生命周期由外层管理）。
        shop_pk: 店铺主键 shop.id（对应设计签名中的 shop_id），限定检索范围。
        query: 客服知识检索查询文本；提供时按 jieba 分词命中标题 / 内容 / 标签。
        goods_id: 商品业务标识；提供时商品知识按其精确匹配。
        limit: 结果上限；商品知识与客服知识各自不超过该值，非法时回退默认值。
        tags: 客服知识标签筛选条件（列表或逗号分隔字符串）；提供时按标签筛选。

    Returns:
        ``KbSearchResult``，含 product_knowledge 与 customer_service_knowledge
        两个列表，长度均不超过 limit。
    """
    max_count = _normalize_limit(limit)

    # ------------------------------------------------------------------
    # 商品知识检索：限定店铺 + 仅启用（status=1）+ goods_id 精确匹配（可选）
    # ------------------------------------------------------------------
    product_filters: dict[str, object] = {"shop_pk": shop_pk, "status": 1}
    if goods_id is not None and str(goods_id).strip() != "":
        product_filters["goods_id"] = goods_id
    product_repo: Repository[ProductKnowledge] = Repository(ProductKnowledge, session)
    product_records = product_repo.list(filters=product_filters, limit=max_count)

    # ------------------------------------------------------------------
    # 客服知识检索：限定店铺 + 仅启用（enabled=True），再按分词 / 标签二次筛选
    # ------------------------------------------------------------------
    cs_repo: Repository[CustomerServiceKnowledge] = Repository(
        CustomerServiceKnowledge, session
    )
    cs_enabled = cs_repo.list(
        filters={"shop_pk": shop_pk, "enabled": True},
        order_by=False,
    )

    want_tags = _split_tags(tags) if isinstance(tags, str) else [
        t.strip().lower() for t in (tags or []) if str(t).strip()
    ]
    tokens = _tokenize(query) if query else []

    cs_matched: list[CustomerServiceKnowledge]
    if tokens:
        # 提供查询：保留命中至少一个分词的记录，按命中分词数降序、id 升序排序
        scored = [
            (rec, _match_token_count(rec, tokens)) for rec in cs_enabled
        ]
        scored = [(rec, score) for rec, score in scored if score > 0]
        scored.sort(key=lambda pair: (-pair[1], getattr(pair[0], "id", 0)))
        cs_matched = [rec for rec, _ in scored]
    else:
        # 未提供查询：返回启用记录（保持稳定顺序），供按标签筛选或全量取前 N
        cs_matched = list(cs_enabled)

    # 标签筛选（需求 10.4）：提供 tags 时仅保留命中任一标签的记录
    if want_tags:
        cs_matched = [rec for rec in cs_matched if _filter_by_tags(rec, want_tags)]

    # 结果不超 limit（需求 10.3）
    cs_matched = cs_matched[:max_count]

    return KbSearchResult(
        product_knowledge=product_records,
        customer_service_knowledge=cs_matched,
    )


__all__ = [
    "KbSearchResult",
    "DEFAULT_SEARCH_LIMIT",
    "search",
]
