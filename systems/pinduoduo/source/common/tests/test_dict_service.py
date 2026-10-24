# -*- coding: utf-8 -*-
"""
common.tests.test_dict_service —— 数据字典服务单元测试
======================================================
本文件用途：以 SQLite 内存库验证 ``common.services.dict_service`` 的查询能力
与初始数据幂等登记能力（任务 2.14；规范 15 / 需求 24.7）：
- 初始数据登记后字典项数量与 DICT_SEED_DATA 一致；
- 二次登记幂等：记录总数不变、不重复插入；
- 按类型查询、key->label 映射、单个 label 查询、分组查询正确；
- 仅返回启用项并按 order_no 升序。
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from common.models.base import Base
from common.models.setting_models import SysDict
from common.services.dict_seed_data import DICT_SEED_DATA
from common.services.dict_service import DictService, register_dict_initial_data


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


def _total_seed_count() -> int:
    """统计初始数据应登记的字典项总数。"""
    return sum(len(items) for items in DICT_SEED_DATA.values())


def test_register_initial_data_inserts_all(session: Session) -> None:
    """首次登记应插入全部初始字典项，数量与种子数据一致。"""
    inserted = register_dict_initial_data(session)
    session.commit()

    assert inserted == _total_seed_count()
    total = session.query(SysDict).count()
    assert total == _total_seed_count()


def test_register_initial_data_is_idempotent(session: Session) -> None:
    """二次登记应幂等：不新增、总数不变。"""
    register_dict_initial_data(session)
    session.commit()
    total_after_first = session.query(SysDict).count()

    inserted_second = register_dict_initial_data(session)
    session.commit()
    total_after_second = session.query(SysDict).count()

    assert inserted_second == 0
    assert total_after_second == total_after_first


def test_register_backfills_missing_item(session: Session) -> None:
    """缺失项应被补齐：删去一项后重新登记仅补该项。"""
    register_dict_initial_data(session)
    session.commit()

    # 移除一个已存在字典项，模拟「缺失」
    item = session.query(SysDict).filter_by(dict_type="match_type", dict_key="regex").one()
    session.delete(item)
    session.commit()

    inserted = register_dict_initial_data(session)
    session.commit()

    assert inserted == 1
    assert session.query(SysDict).count() == _total_seed_count()


def test_list_by_type_sorted_and_enabled_only(session: Session) -> None:
    """按类型查询应仅返回启用项并按 order_no 升序。"""
    register_dict_initial_data(session)
    session.commit()
    svc = DictService(session)

    items = svc.list_by_type("conn_state")
    keys = [i.dict_key for i in items]
    assert keys == ["connected", "connecting", "disconnected", "reconnecting", "error"]

    # 停用一项后不应再返回
    svc.repo.update_by(
        filters={"dict_type": "conn_state", "dict_key": "error"},
        values={"enabled": False},
    )
    session.commit()
    items_after = svc.list_by_type("conn_state")
    assert "error" not in [i.dict_key for i in items_after]


def test_get_label_and_map(session: Session) -> None:
    """key->label 映射与单个 label 查询应返回正确中文文案。"""
    register_dict_initial_data(session)
    session.commit()
    svc = DictService(session)

    label_map = svc.get_label_map("match_type")
    assert label_map == {"full": "全匹配", "contains": "包含", "regex": "正则"}

    assert svc.get_label("login_state", "relogin") == "需重新登录"
    assert svc.get_label("login_state", "not_exist", default="未知") == "未知"


def test_group_by_types(session: Session) -> None:
    """分组查询应按类型返回各自的启用字典项。"""
    register_dict_initial_data(session)
    session.commit()
    svc = DictService(session)

    grouped = svc.group_by_types(["reply_type", "run_result"])
    assert set(grouped.keys()) == {"reply_type", "run_result"}
    assert [i.dict_key for i in grouped["reply_type"]] == ["text", "image"]
    assert [i.dict_key for i in grouped["run_result"]] == ["success", "failed"]
