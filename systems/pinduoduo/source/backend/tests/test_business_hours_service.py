# -*- coding: utf-8 -*-
"""
backend.tests.test_business_hours_service —— 营业时间配置服务单元测试
====================================================================
本文件用途：对 backend 营业时间配置业务服务（app.services.business_hours_service）
进行单元测试，覆盖需求 11.1（营业时间起止时刻配置持久化）的核心场景：

- 首次配置：持久化起止时刻并返回成功响应（需求 11.1）；
- 重复配置 upsert 幂等：同一店铺再次配置覆盖更新，记录数恒为 1；
- 时刻格式：支持 HH:MM 与 HH:MM:SS，统一序列化为 HH:MM:SS（北京时间口径）；
- 跨午夜区间：允许 end_time 早于 start_time（如 22:00~02:00）；
- 未配置 / 空时刻：均为空表示未配置，查询返回 data=None（默认全天，需求 11.4）；
- 非法时刻：返回 success=false 与中文提示，HTTP 恒 200（统一响应体）；
- 非法店铺主键：返回 success=false 与中文提示。

测试方案：pytest + 内存 SQLite（夹具见 conftest.py）。在模块顶部导入
BusinessHours 模型，确保其表登记进 Base.metadata 并在建表时一并创建。
"""
from __future__ import annotations

import pytest

from app.services import business_hours_service
from common.db.repository import Repository
from common.models.config_models import BusinessHours
from common.models.shop_models import Shop
from common.models.user_models import SysRole, SysUser
from common.utils.security import hash_password

# 测试用店铺主键（与具体店铺记录解耦，营业时间表按 shop_pk 普通列关联）。
_SHOP_PK = 1001


@pytest.fixture()
def bh_env(db_session):
    """预置管理员用户与归属店铺（pk=_SHOP_PK），用于数据范围隔离校验通过。

    营业时间为店铺级设置，服务层需校验店铺存在且在操作用户可见范围内
    （需求 3.7 / 规范 42a）。这里以管理员身份操作（不受归属限制），并预置
    id=_SHOP_PK 的店铺，使配置 / 查询可正常进行。
    """
    role_admin = SysRole(role_name="管理员BH", is_admin=True, status=1)
    db_session.add(role_admin)
    db_session.flush()
    admin = SysUser(
        username="bh_admin",
        password_hash=hash_password("bh-admin-123"),
        role_id=role_admin.id,
        status=1,
    )
    db_session.add(admin)
    db_session.flush()
    db_session.add(
        Shop(
            id=_SHOP_PK,
            shop_id="pdd-bh",
            shop_name="店铺BH",
            owner_user_id=admin.id,
            status=1,
        )
    )
    db_session.flush()
    db_session.commit()
    return {"operator_id": admin.id}


def test_configure_persists_start_and_end_time(db_session, bh_env):
    """首次配置：持久化起止时刻并返回成功响应（需求 11.1）。"""
    resp = business_hours_service.configure_business_hours(
        db_session,
        shop_pk=_SHOP_PK,
        start_time="08:30",
        end_time="22:00",
        operator_id=bh_env["operator_id"],
    )
    assert resp.success is True
    assert resp.data["shop_pk"] == _SHOP_PK
    # 统一序列化为 HH:MM:SS。
    assert resp.data["start_time"] == "08:30:00"
    assert resp.data["end_time"] == "22:00:00"
    assert resp.data["enabled"] is True

    # 落库校验：恰好一条记录。
    records = Repository(BusinessHours, db_session).list(
        filters={"shop_pk": _SHOP_PK}
    )
    assert len(records) == 1


def test_configure_is_upsert_idempotent(db_session, bh_env):
    """重复配置 upsert 幂等：同一店铺再次配置覆盖更新，记录数恒为 1。"""
    op = bh_env["operator_id"]
    business_hours_service.configure_business_hours(
        db_session, shop_pk=_SHOP_PK, start_time="08:00", end_time="20:00", operator_id=op
    )
    resp = business_hours_service.configure_business_hours(
        db_session, shop_pk=_SHOP_PK, start_time="09:00", end_time="21:30", operator_id=op
    )
    assert resp.success is True
    # 覆盖更新为最后一次写入。
    assert resp.data["start_time"] == "09:00:00"
    assert resp.data["end_time"] == "21:30:00"

    # 记录数恒为 1（幂等）。
    count = Repository(BusinessHours, db_session).count(filters={"shop_pk": _SHOP_PK})
    assert count == 1


def test_configure_accepts_hms_format(db_session, bh_env):
    """时刻格式：支持 HH:MM:SS 输入并原样保留秒。"""
    resp = business_hours_service.configure_business_hours(
        db_session,
        shop_pk=_SHOP_PK,
        start_time="08:15:30",
        end_time="22:45:10",
        operator_id=bh_env["operator_id"],
    )
    assert resp.success is True
    assert resp.data["start_time"] == "08:15:30"
    assert resp.data["end_time"] == "22:45:10"


def test_configure_allows_cross_midnight(db_session, bh_env):
    """跨午夜区间：允许 end_time 早于 start_time（如 22:00~02:00）。"""
    resp = business_hours_service.configure_business_hours(
        db_session,
        shop_pk=_SHOP_PK,
        start_time="22:00",
        end_time="02:00",
        operator_id=bh_env["operator_id"],
    )
    assert resp.success is True
    assert resp.data["start_time"] == "22:00:00"
    assert resp.data["end_time"] == "02:00:00"


def test_configure_empty_times_means_unconfigured(db_session, bh_env):
    """未配置：起止时刻均为空表示未配置，落库 start/end 为 None。"""
    resp = business_hours_service.configure_business_hours(
        db_session,
        shop_pk=_SHOP_PK,
        start_time=None,
        end_time="",
        operator_id=bh_env["operator_id"],
    )
    assert resp.success is True
    assert resp.data["start_time"] is None
    assert resp.data["end_time"] is None


def test_configure_invalid_time_returns_error(db_session, bh_env):
    """非法时刻：返回 success=false 与中文提示，HTTP 恒 200（统一响应体）。"""
    resp = business_hours_service.configure_business_hours(
        db_session,
        shop_pk=_SHOP_PK,
        start_time="25:99",
        end_time="22:00",
        operator_id=bh_env["operator_id"],
    )
    assert resp.success is False
    assert resp.data is None
    assert "营业开始时刻" in resp.message


def test_configure_invalid_shop_pk_returns_error(db_session):
    """非法店铺主键：返回 success=false 与中文提示。"""
    resp = business_hours_service.configure_business_hours(
        db_session, shop_pk=0, start_time="08:00", end_time="20:00"
    )
    assert resp.success is False
    assert resp.data is None
    assert "店铺主键" in resp.message


def test_get_business_hours_returns_none_when_unconfigured(db_session, bh_env):
    """查询未配置但存在的店铺：返回 success=true 且 data=None（默认全天，需求 11.4）。"""
    resp = business_hours_service.get_business_hours(
        db_session, shop_pk=_SHOP_PK, operator_id=bh_env["operator_id"]
    )
    assert resp.success is True
    assert resp.data is None


def test_get_business_hours_returns_configured(db_session, bh_env):
    """查询已配置店铺：返回已保存的营业时间配置。"""
    op = bh_env["operator_id"]
    business_hours_service.configure_business_hours(
        db_session, shop_pk=_SHOP_PK, start_time="08:00", end_time="20:00", operator_id=op
    )
    resp = business_hours_service.get_business_hours(
        db_session, shop_pk=_SHOP_PK, operator_id=op
    )
    assert resp.success is True
    assert resp.data["start_time"] == "08:00:00"
    assert resp.data["end_time"] == "20:00:00"
