# -*- coding: utf-8 -*-
"""
common.services.dict_service —— 数据字典服务（sys_dict）
========================================================
本文件用途：提供「拼多多自动回复」系统数据字典 ``sys_dict`` 的查询与初始
数据登记能力（规范 15：枚举值入字典表，前端从字典查中文展示；需求 24.7 /
4.9 / 5.7 / 13.4 / 17.4）。

提供能力：
- 查询：按 ``dict_type`` 查询字典项列表、查询 key->中文 label 映射、查询
  单个 key 的中文 label、按多个类型分组批量查询，供前端展示中文文案；
  仅返回启用（enabled=True）的字典项，并按 ``order_no`` 升序排序。
- 登记：``register_dict_initial_data(session)`` 幂等补齐 design.md 列出的
  全部枚举初始数据（见 dict_seed_data.DICT_SEED_DATA）；已存在的
  (dict_type, dict_key) 不重复插入，缺失才补，供启动自检迁移器（任务 2.12）
  调用，不影响历史数据（规范 14）。

设计说明：
- 所有数据访问经 ``common.db.repository.Repository``（参数化查询，规范 16），
  本文件不书写任何原生 SQL。
- ``DictService`` 以「会话」构造，单实例服务一次事务；查询方法不提交事务，
  登记方法仅 ``flush``，由外层（session_scope / lifespan）统一 commit。
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from common.db.init_database import register_dict_initializer
from common.db.repository import Repository
from common.models.setting_models import SysDict
from common.services.dict_seed_data import DICT_SEED_DATA


class DictService:
    """数据字典服务：封装 sys_dict 的查询与初始数据幂等登记。

    以当前事务性会话构造，所有读写经通用仓储层完成参数化查询（规范 16）。
    查询接口仅返回启用项并按 order_no 升序，供前端展示中文文案。
    """

    def __init__(self, session: Session) -> None:
        """构造字典服务。

        Args:
            session: 当前事务性会话（生命周期由外层管理）。
        """
        self.session = session
        self.repo: Repository[SysDict] = Repository(SysDict, session)

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------
    def list_by_type(self, dict_type: str) -> list[SysDict]:
        """按字典类型查询启用的字典项列表，按 order_no 升序。

        Args:
            dict_type: 字典类型（枚举分组键，如 ``match_type``）。

        Returns:
            启用的 ``SysDict`` 实例列表，按 order_no 升序；无数据返回空列表。
        """
        return self.repo.list(
            filters={"dict_type": dict_type, "enabled": True},
            order_by=SysDict.order_no,
            desc_order=False,
        )

    def get_label_map(self, dict_type: str) -> dict[str, str]:
        """查询某字典类型的「键 -> 中文标签」映射，供前端中文展示。

        Args:
            dict_type: 字典类型。

        Returns:
            ``{dict_key: dict_label}`` 有序映射（按 order_no 升序）。
        """
        return {item.dict_key: item.dict_label for item in self.list_by_type(dict_type)}

    def get_label(
        self, dict_type: str, dict_key: str, default: str | None = None
    ) -> str | None:
        """查询单个字典项的中文标签。

        Args:
            dict_type: 字典类型。
            dict_key: 字典键。
            default: 未命中（或字典项被停用）时返回的默认值。

        Returns:
            命中的中文标签；未命中返回 ``default``。
        """
        item = self.repo.get_by(
            dict_type=dict_type, dict_key=dict_key, enabled=True
        )
        return item.dict_label if item is not None else default

    def group_by_types(
        self, dict_types: list[str]
    ) -> dict[str, list[SysDict]]:
        """按多个字典类型分组批量查询启用字典项。

        供前端一次性拉取多类枚举的中文文案（如表单初始化）。

        Args:
            dict_types: 字典类型列表。

        Returns:
            ``{dict_type: [SysDict, ...]}`` 映射；每组按 order_no 升序。
        """
        return {dict_type: self.list_by_type(dict_type) for dict_type in dict_types}

    # ------------------------------------------------------------------
    # 初始数据登记（幂等补齐，供启动自检迁移器调用）
    # ------------------------------------------------------------------
    def register_initial_data(self) -> int:
        """幂等登记字典初始数据：仅补齐缺失项，已存在项一律不改动。

        遍历 ``DICT_SEED_DATA`` 中全部 (dict_type, dict_key)：
        - 不存在的字典项 → 插入（enabled=True）；
        - 已存在的字典项 → **完全不改动**（保留管理员对中文文案 / 排序 /
          启停用的历史修改）。

        说明（规范 14：启动自检不得影响历史数据）：早期实现对已存在项做了
        无条件 upsert 覆盖（含把 ``enabled`` 强制改回 True），会把管理员手动
        停用 / 改名 / 调序的字典项在重启后静默还原。此处改为「仅插入缺失」，
        规范 14 仅要求自动补齐缺失字典 / 初始数据，并不要求覆盖既有项；以
        「对数据更保守」为准，绝不覆盖历史数据。

        Returns:
            本次新插入的字典项数量（已存在项不计入）。
        """
        inserted = 0
        for dict_type, items in DICT_SEED_DATA.items():
            for dict_key, dict_label, order_no in items:
                existing = self.repo.get_by(dict_type=dict_type, dict_key=dict_key)
                if existing is not None:
                    # 已存在：保护历史数据，不覆盖文案 / 排序 / 启停用状态（规范 14）。
                    continue
                # 仅插入缺失项（enabled=True）。
                self.repo.create(
                    dict_type=dict_type,
                    dict_key=dict_key,
                    dict_label=dict_label,
                    order_no=order_no,
                    enabled=True,
                )
                inserted += 1
        return inserted


def register_dict_initial_data(session: Session) -> int:
    """便捷函数：在给定会话中幂等登记字典初始数据，供迁移器调用。

    由启动自检迁移器（任务 2.12）在建表 / 补字段后调用，完成「缺失字典补齐」。
    本函数仅 ``flush``（经仓储层），事务提交由外层（backend lifespan 的
    session_scope）负责。

    Args:
        session: 当前事务性会话。

    Returns:
        本次新插入的字典项数量。
    """
    return DictService(session).register_initial_data()


# 模块导入时向启动自检迁移器注册字典初始化钩子（任务 2.12 的扩展点）：
# 迁移器在建表 / 补字段后回调本钩子，幂等补齐枚举字典初始数据。
# register_dict_initializer 自身去重，重复导入不会重复注册。
register_dict_initializer(register_dict_initial_data)


__all__ = [
    "DictService",
    "register_dict_initial_data",
]
