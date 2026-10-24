# -*- coding: utf-8 -*-
"""
backend.app.services.backup_service —— 数据库备份导出与导入恢复业务服务
======================================================================
本文件用途：实现 backend 服务的「数据库备份导出与导入恢复」业务逻辑（任务
8.3），供 backup 路由复用，满足需求 21.16：

- ``generate_backup(session)``：遍历系统全部数据表，导出为单个 JSON 备份载荷
  （含版本号、北京时间生成时间、各表全部行数据），并返回「备份文件名 + 文件
  内容字节」，供路由以附件形式下载。
- ``restore_backup(session, raw_bytes)``：校验上传的备份文件并在「不破坏现有
  数据完整性」的前提下恢复数据：
  1. **先整体校验后写入**：解析 JSON、校验版本、校验表名与列名均属系统已知
     结构、每行须含主键；任一校验不通过则在「未写入任何数据」的情况下返回失败
     （需求 21.16「校验文件」）。
  2. **按主键 upsert 合并恢复**：对每行按主键查找——存在则更新、不存在则插入；
     **绝不删除任何现有数据**（规范 11 / 需求 24.6），不在备份中的现有记录原样
     保留，故现有数据完整性不被破坏（需求 21.16）。
  3. **整体事务**：恢复过程中任一行写入异常即回滚本次恢复，保证「要么全部成功
     恢复、要么不改动」，不留下半提交脏数据。

关键约束（开发规范）：
- 统一响应体由 common.schemas.common 构造，HTTP 恒 200（规范 1-3 / 需求 24.1）。
- 所有数据访问经 SQLAlchemy ORM / Core 参数化执行，禁止拼接 SQL（规范 16）。
- 禁止物理删除业务数据：恢复为「插入或更新」的合并语义，不做任何删除（规范 11）。
- 时间统一北京时间（规范 17 / 需求 24.8）：备份生成时间取 ``now_beijing_naive``。
- 导入置顶（规范 51）；中文注释（规范 37）；单文件 ≤500 行（规范 35）。
- 权限「仅管理员」由路由层统一拦截（需求 21.17）。
"""
from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import Date, DateTime, Numeric, Table, select
from sqlalchemy.orm import Session

from app.core.business_codes import CODE_BACKUP_INVALID
from common.models.base import Base
from common.schemas.common import ApiResponse, error_response, success_response
from common.utils.time_utils import now_beijing_naive

# 备份文件格式版本号：恢复时仅接受已知版本，便于未来结构演进时做兼容判断。
BACKUP_VERSION: str = "1.0"

# 备份载荷的固定顶层键名（导出 / 校验共用，集中管理避免散落）。
_KEY_VERSION: str = "version"
_KEY_GENERATED_AT: str = "generated_at"
_KEY_TABLES: str = "tables"

# 备份文件名前缀；实际文件名追加北京时间戳，便于区分多次备份。
_FILE_PREFIX: str = "pdd_backup_"


# ----------------------------------------------------------------------
# 值序列化 / 反序列化（按列类型在备份 JSON 与 Python 值间互转）
# ----------------------------------------------------------------------
def _serialize_value(value: Any) -> Any:
    """将单元格值序列化为可 JSON 化的形式（时间转 ISO 串、Decimal 转字符串）。

    Args:
        value: 数据库行的某列原始值。

    Returns:
        可被 ``json.dumps`` 序列化的值。
    """
    if isinstance(value, (datetime, date)):
        # 时间统一以 ISO 8601 字符串存储，恢复时按列类型解析回 datetime/date。
        return value.isoformat()
    if isinstance(value, Decimal):
        # Decimal 以字符串保存，避免浮点精度丢失。
        return str(value)
    if isinstance(value, bytes):
        # 二进制以 latin-1 可逆地转为字符串（当前模型无二进制列，作兜底）。
        return value.decode("latin-1")
    return value


def _deserialize_value(column_type: Any, value: Any) -> Any:
    """按目标列类型将备份 JSON 中的值反序列化为对应 Python 值。

    Args:
        column_type: 目标列的 SQLAlchemy 类型实例。
        value: 备份中的原始 JSON 值。

    Returns:
        适配目标列类型的 Python 值。
    """
    if value is None:
        return None
    # 时间类：ISO 字符串还原为 datetime / date。
    if isinstance(column_type, DateTime) and isinstance(value, str):
        return datetime.fromisoformat(value)
    if isinstance(column_type, Date) and isinstance(value, str):
        return date.fromisoformat(value)
    # 数值类：字符串还原为 Decimal，保持精度。
    if isinstance(column_type, Numeric) and isinstance(value, (str, int, float)):
        return Decimal(str(value))
    return value


# ----------------------------------------------------------------------
# 表清单（系统全部已注册数据表）
# ----------------------------------------------------------------------
def _all_tables() -> List[Table]:
    """返回系统全部已注册数据表（按依赖排序，无外键时为稳定顺序）。

    取自统一声明式基类 ``Base.metadata``，与启动自检迁移器同源，保证备份覆盖
    全部业务表。

    Returns:
        ``Table`` 对象列表。
    """
    return list(Base.metadata.sorted_tables)


def _table_map() -> Dict[str, Table]:
    """返回「表名 -> Table」映射，便于恢复时按名查找与校验。"""
    return {table.name: table for table in _all_tables()}


# ----------------------------------------------------------------------
# 导出备份（需求 21.16 前半：生成备份文件供下载）
# ----------------------------------------------------------------------
def build_backup_payload(session: Session) -> Dict[str, Any]:
    """构建备份载荷字典（含版本、生成时间与各表全部行数据）。

    Args:
        session: 数据库会话。

    Returns:
        备份载荷字典：{version, generated_at, tables: {表名: [行字典...]}}。
    """
    tables_data: Dict[str, List[Dict[str, Any]]] = {}
    for table in _all_tables():
        rows: List[Dict[str, Any]] = []
        # 经 SQLAlchemy Core select 全表读取，参数化执行（规范 16）。
        result = session.execute(select(table)).mappings().all()
        for row in result:
            rows.append({key: _serialize_value(val) for key, val in row.items()})
        tables_data[table.name] = rows
    return {
        _KEY_VERSION: BACKUP_VERSION,
        _KEY_GENERATED_AT: now_beijing_naive().isoformat(),
        _KEY_TABLES: tables_data,
    }


def generate_backup(session: Session) -> Tuple[str, bytes]:
    """生成备份文件名与文件内容字节，供路由以附件形式下载（需求 21.16）。

    Args:
        session: 数据库会话。

    Returns:
        二元组 (文件名, 文件内容字节)；内容为 UTF-8 编码的 JSON 文本。
    """
    payload = build_backup_payload(session)
    content = json.dumps(payload, ensure_ascii=False, indent=2)
    # 文件名追加北京时间戳（精确到秒），避免多次备份重名。
    stamp = now_beijing_naive().strftime("%Y%m%d_%H%M%S")
    filename = f"{_FILE_PREFIX}{stamp}.json"
    return filename, content.encode("utf-8")


# ----------------------------------------------------------------------
# 校验备份文件（需求 21.16：导入前先校验）
# ----------------------------------------------------------------------
def _validate_payload(
    raw_bytes: bytes,
) -> Tuple[Optional[Dict[str, Any]], Optional[ApiResponse]]:
    """解析并校验备份文件内容，校验通过返回载荷，否则返回失败响应。

    校验项（任一不通过即判定文件非法，且此时尚未写入任何数据）：
    - 可被 UTF-8 解码并解析为 JSON 对象；
    - 顶层为字典且版本号等于受支持的 ``BACKUP_VERSION``；
    - ``tables`` 为字典；其每个表名均属系统已知表；
    - 每个表的值为「行字典」列表；每行的列名均属该表已知列；每行须含全部主键列。

    Args:
        raw_bytes: 上传的备份文件原始字节。

    Returns:
        二元组 (载荷字典, 失败响应)：校验通过返回 (payload, None)；否则
        (None, 失败响应体)。
    """
    # 1) 解码与 JSON 解析。
    try:
        text = raw_bytes.decode("utf-8")
        payload = json.loads(text)
    except (UnicodeDecodeError, ValueError):
        return None, error_response(CODE_BACKUP_INVALID, "备份文件格式无效，无法解析")

    # 2) 顶层结构与版本校验。
    if not isinstance(payload, dict):
        return None, error_response(CODE_BACKUP_INVALID, "备份文件格式无效")
    if payload.get(_KEY_VERSION) != BACKUP_VERSION:
        return None, error_response(
            CODE_BACKUP_INVALID, "备份文件版本不受支持，无法恢复"
        )
    tables = payload.get(_KEY_TABLES)
    if not isinstance(tables, dict):
        return None, error_response(CODE_BACKUP_INVALID, "备份文件缺少有效的表数据")

    # 3) 逐表逐行校验表名 / 列名 / 主键。
    table_map = _table_map()
    for table_name, rows in tables.items():
        table = table_map.get(table_name)
        if table is None:
            return None, error_response(
                CODE_BACKUP_INVALID, f"备份文件包含未知数据表：{table_name}"
            )
        if not isinstance(rows, list):
            return None, error_response(
                CODE_BACKUP_INVALID, f"数据表 {table_name} 的行数据格式无效"
            )
        known_columns = set(table.columns.keys())
        pk_columns = [col.name for col in table.primary_key.columns]
        for row in rows:
            if not isinstance(row, dict):
                return None, error_response(
                    CODE_BACKUP_INVALID, f"数据表 {table_name} 含非法行数据"
                )
            unknown = set(row.keys()) - known_columns
            if unknown:
                return None, error_response(
                    CODE_BACKUP_INVALID,
                    f"数据表 {table_name} 含未知字段：{','.join(sorted(unknown))}",
                )
            missing_pk = [pk for pk in pk_columns if row.get(pk) is None]
            if missing_pk:
                return None, error_response(
                    CODE_BACKUP_INVALID,
                    f"数据表 {table_name} 存在缺少主键的记录，无法恢复",
                )
    return payload, None


# ----------------------------------------------------------------------
# 导入恢复（需求 21.16 后半：校验并在不破坏现有数据完整性前提下恢复）
# ----------------------------------------------------------------------
def _restore_row(session: Session, table: Table, row: Dict[str, Any]) -> bool:
    """按主键 upsert 单行（存在则更新、不存在则插入），返回是否为新插入。

    绝不删除任何数据：仅做「插入或更新」的合并恢复（规范 11）。

    Args:
        session: 数据库会话。
        table: 目标表。
        row: 备份中的行字典（列名 -> 值）。

    Returns:
        True 表示该行为新插入；False 表示更新了已存在记录。
    """
    # 按目标列类型反序列化行值（时间 / Decimal 等）。
    values = {
        name: _deserialize_value(table.columns[name].type, value)
        for name, value in row.items()
    }
    pk_columns = [col.name for col in table.primary_key.columns]
    # 组装主键等值查询条件（参数化）。
    conditions = [table.columns[pk] == values[pk] for pk in pk_columns]
    existing = session.execute(
        select(table).where(*conditions).limit(1)
    ).first()
    if existing is None:
        session.execute(table.insert().values(**values))
        return True
    # 更新非主键列；主键作为定位条件不参与更新。
    update_values = {k: v for k, v in values.items() if k not in pk_columns}
    if update_values:
        session.execute(table.update().where(*conditions).values(**update_values))
    return False


def restore_backup(session: Session, raw_bytes: bytes) -> ApiResponse:
    """校验并恢复备份数据（需求 21.16）。

    先整体校验文件；校验不通过则在未写入任何数据的情况下返回失败。校验通过后
    按主键 upsert 合并恢复全部行，恢复过程任一异常即回滚本次恢复，保证现有数据
    完整性不被破坏。

    Args:
        session: 数据库会话。
        raw_bytes: 上传的备份文件原始字节。

    Returns:
        统一响应体：成功返回 data={inserted, updated, tables}；失败返回中文提示。
    """
    payload, denied = _validate_payload(raw_bytes)
    if denied is not None:
        return denied

    table_map = _table_map()
    tables: Dict[str, Any] = payload[_KEY_TABLES]  # 校验已确保为合法结构
    inserted = 0
    updated = 0
    affected_tables = 0
    try:
        for table_name, rows in tables.items():
            if not rows:
                continue
            affected_tables += 1
            table = table_map[table_name]
            for row in rows:
                if _restore_row(session, table, row):
                    inserted += 1
                else:
                    updated += 1
        session.flush()
        session.commit()
    except Exception as exc:  # noqa: BLE001 —— 恢复异常一律回滚，保护现有数据
        # 整体回滚，保证「要么全部成功、要么不改动」，不留半提交脏数据。
        session.rollback()
        return error_response(
            CODE_BACKUP_INVALID, f"备份恢复失败，已回滚未改动现有数据：{exc}"
        )

    data = {
        "inserted": inserted,
        "updated": updated,
        "tables": affected_tables,
    }
    return success_response(data=data, message="备份恢复成功")


__all__ = [
    "BACKUP_VERSION",
    "build_backup_payload",
    "generate_backup",
    "restore_backup",
]
