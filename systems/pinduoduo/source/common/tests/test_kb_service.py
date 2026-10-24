# -*- coding: utf-8 -*-
"""
common.tests.test_kb_service —— 知识库检索服务单元测试
======================================================
本文件用途：以 SQLite 内存库验证 ``common.services.kb_service.search``
的检索约束（任务 6.9；需求 9.4 / 10.3 / 10.4 / 10.5）：
- 限定店铺：跨店铺数据不返回；
- 客服知识仅启用：停用记录检索时不返回；
- 结果不超 limit：商品知识与客服知识各自不超过 limit；
- 按 goods_id 精确匹配商品知识；
- 按标签筛选客服知识；
- jieba 分词命中标题 / 内容 / 标签。
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from common.models.base import Base
from common.models.knowledge_models import CustomerServiceKnowledge, ProductKnowledge
from common.services.kb_service import DEFAULT_SEARCH_LIMIT, search


@pytest.fixture()
def session() -> Session:
    """提供基于 SQLite 内存库的事务性会话，并按模型建表。"""
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, class_=Session, expire_on_commit=False, future=True)
    sess = factory()
    try:
        yield sess
    finally:
        sess.close()
        engine.dispose()


def _add_product(session: Session, shop_pk: int, goods_id: str, status: int = 1) -> None:
    """插入一条商品知识。"""
    session.add(
        ProductKnowledge(
            shop_pk=shop_pk,
            goods_id=goods_id,
            goods_name=f"商品{goods_id}",
            extracted_content="规格颜色尺码",
            status=status,
        )
    )


def _add_cs(
    session: Session,
    shop_pk: int,
    title: str,
    content: str,
    tags: str | None = None,
    enabled: bool = True,
) -> None:
    """插入一条客服知识。"""
    session.add(
        CustomerServiceKnowledge(
            shop_pk=shop_pk,
            title=title,
            content=content,
            tags=tags,
            enabled=enabled,
        )
    )


def test_search_limits_to_shop(session: Session) -> None:
    """检索应限定店铺，跨店铺数据不返回。"""
    _add_product(session, shop_pk=1, goods_id="g1")
    _add_product(session, shop_pk=2, goods_id="g2")
    _add_cs(session, shop_pk=1, title="退换货政策", content="七天无理由")
    _add_cs(session, shop_pk=2, title="退换货政策", content="七天无理由")
    session.commit()

    result = search(session, shop_pk=1)
    assert all(p.shop_pk == 1 for p in result.product_knowledge)
    assert all(c.shop_pk == 1 for c in result.customer_service_knowledge)


def test_cs_only_enabled(session: Session) -> None:
    """客服知识仅返回启用项，停用记录不返回。"""
    _add_cs(session, shop_pk=1, title="物流时效", content="48小时发货", enabled=True)
    _add_cs(session, shop_pk=1, title="物流停用", content="48小时发货", enabled=False)
    session.commit()

    result = search(session, shop_pk=1, query="物流")
    titles = [c.title for c in result.customer_service_knowledge]
    assert "物流时效" in titles
    assert "物流停用" not in titles


def test_product_only_enabled(session: Session) -> None:
    """商品知识仅返回启用项（status=1）。"""
    _add_product(session, shop_pk=1, goods_id="g1", status=1)
    _add_product(session, shop_pk=1, goods_id="g0", status=0)
    session.commit()

    result = search(session, shop_pk=1)
    gids = [p.goods_id for p in result.product_knowledge]
    assert "g1" in gids
    assert "g0" not in gids


def test_goods_id_exact_match(session: Session) -> None:
    """提供 goods_id 时商品知识按其精确匹配。"""
    _add_product(session, shop_pk=1, goods_id="123")
    _add_product(session, shop_pk=1, goods_id="456")
    session.commit()

    result = search(session, shop_pk=1, goods_id="123")
    gids = [p.goods_id for p in result.product_knowledge]
    assert gids == ["123"]


def test_result_not_exceed_limit(session: Session) -> None:
    """商品知识与客服知识各自数量不超过 limit。"""
    for i in range(10):
        _add_product(session, shop_pk=1, goods_id=f"g{i}")
        _add_cs(session, shop_pk=1, title=f"物流问题{i}", content="发货时效说明")
    session.commit()

    result = search(session, shop_pk=1, query="物流", limit=3)
    assert len(result.product_knowledge) <= 3
    assert len(result.customer_service_knowledge) <= 3


def test_tag_filter(session: Session) -> None:
    """按标签筛选客服知识，仅返回命中标签的记录。"""
    _add_cs(session, shop_pk=1, title="退货", content="退货说明", tags="售后,退换货")
    _add_cs(session, shop_pk=1, title="发货", content="发货说明", tags="物流")
    session.commit()

    result = search(session, shop_pk=1, tags="售后")
    titles = [c.title for c in result.customer_service_knowledge]
    assert titles == ["退货"]


def test_query_tokenize_match(session: Session) -> None:
    """jieba 分词命中标题 / 内容 / 标签任一即匹配。"""
    _add_cs(session, shop_pk=1, title="退换货政策", content="支持七天无理由退货")
    _add_cs(session, shop_pk=1, title="优惠活动", content="满减促销")
    session.commit()

    result = search(session, shop_pk=1, query="退货政策")
    titles = [c.title for c in result.customer_service_knowledge]
    assert "退换货政策" in titles
    assert "优惠活动" not in titles


def test_default_limit_applied(session: Session) -> None:
    """非法 limit 回退默认值。"""
    for i in range(DEFAULT_SEARCH_LIMIT + 5):
        _add_cs(session, shop_pk=1, title=f"物流{i}", content="发货时效")
    session.commit()

    result = search(session, shop_pk=1, query="物流", limit=0)
    assert len(result.customer_service_knowledge) <= DEFAULT_SEARCH_LIMIT
