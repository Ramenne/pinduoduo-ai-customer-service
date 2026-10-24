# -*- coding: utf-8 -*-
"""
backend.app.services.setting_store —— 系统设置键值存储公共工具
==============================================================
本文件用途：抽取「系统设置」分组键值的统一读写逻辑，作为 setting_service
（任务 8.1：主题/分页/基础/品牌/免责声明/二维码/菜单显隐）与
smtp_proxy_service（任务 8.2：SMTP/代理）的公共存储层，避免在多处重复实现
同一套 upsert / 读取逻辑（规范 36 / 52：同一方法不重复实现、优先复用公共能力）。

存储模型说明（common.models.setting_models.SysSetting）：
- 以键值对存储系统级设置：``setting_key`` 为「设置分组键」（如 theme / smtp /
  proxy 等），``setting_value`` 为该分组的 JSON 文本，``scope='global'``，
  ``owner_user_id`` 为空（系统级，非用户维度）。
- 同一分组键按 (setting_key, scope, owner_user_id) 作为业务键 upsert：同一系统
  级设置分组恒为 1 条，重复保存覆盖更新（幂等，需求 24.6）。

实现约束（开发规范）：
- 所有数据访问经 common.db.repository 的参数化查询，禁止拼接 SQL（规范 16）。
- 禁止物理删除业务数据；设置变更经 upsert 覆盖更新（规范 11 / 需求 24.6）。
- 导入置顶（规范 51）；中文注释（规范 37）；单文件 ≤500 行（规范 35）。
"""
from __future__ import annotations

import json
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from common.db.repository import Repository
from common.models.setting_models import SysSetting

# 系统级设置的统一作用域标识（与 SysSetting.scope 约定一致）。
SCOPE_GLOBAL: str = "global"


def get_group(
    session: Session, key: str, defaults: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """读取某分组设置值并与默认值合并（缺失字段以默认值兜底）。

    Args:
        session: 数据库会话。
        key: 设置分组键。
        defaults: 该分组默认值字典；None 表示无默认值（返回纯存储值或空字典）。

    Returns:
        合并默认值后的设置字典（前端可直接使用）；历史脏数据无法解析时退回默认值。
    """
    # 以默认值为底，确保返回结构完整（即便历史数据缺字段）。
    merged: Dict[str, Any] = dict(defaults or {})
    record = Repository(SysSetting, session).get_by(
        setting_key=key, scope=SCOPE_GLOBAL, owner_user_id=None
    )
    if record is None or not record.setting_value:
        return merged
    try:
        stored = json.loads(record.setting_value)
    except (ValueError, TypeError):
        # 历史脏数据无法解析时退回默认值，避免接口报错。
        return merged
    if isinstance(stored, dict):
        merged.update(stored)
    return merged


def save_group(
    session: Session,
    key: str,
    value: Dict[str, Any],
    operator_id: Optional[int] = None,
) -> SysSetting:
    """按分组键 upsert 系统级设置值（幂等覆盖更新）。

    Args:
        session: 数据库会话。
        key: 设置分组键。
        value: 待持久化的设置字典（将序列化为 JSON 文本）。
        operator_id: 操作人用户 ID（仅新建时作为创建人审计字段）。

    Returns:
        upsert 后的设置记录。
    """
    repo = Repository(SysSetting, session)
    # 业务键：系统级设置以 (setting_key, scope=global, owner_user_id=NULL) 唯一。
    biz_keys = {
        "setting_key": key,
        "scope": SCOPE_GLOBAL,
        "owner_user_id": None,
    }
    existing = repo.get_by(**biz_keys)
    serialized = json.dumps(value, ensure_ascii=False)
    if existing is None:
        return repo.create(
            setting_key=key,
            scope=SCOPE_GLOBAL,
            owner_user_id=None,
            setting_value=serialized,
            created_by=operator_id,
        )
    return repo.update(existing.id, setting_value=serialized)


__all__ = [
    "SCOPE_GLOBAL",
    "get_group",
    "save_group",
]
