# -*- coding: utf-8 -*-
"""
common 测试公共夹具与路径配置
==============================
本文件用途：保证以 `common` 顶级包形式导入被测模块（如 common.utils.security）。
common 是公共库（被各服务通过 sys.path 共享复用），其内部模块统一以
`from common.xxx import ...` 方式导入，因此需要把仓库根目录（common 的父目录）
加入 sys.path，使测试无论从何处启动都能正确解析 `common` 包。
"""
import os
import sys

# 仓库根目录 = common 目录的父目录；加入 sys.path 以支持 `import common.*`
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


# ----------------------------------------------------------------------
# SQLite 测试方言适配：让 BigInteger 自增主键在 SQLite 内存库下正常工作
# ----------------------------------------------------------------------
# common 模型主键统一为 BigInteger 自增（规范 9，面向生产 MySQL）。但 SQLite
# 仅对「INTEGER PRIMARY KEY」列自动赋值自增 rowid，对「BIGINT PRIMARY KEY」不会
# 自增，导致插入时 id 为 NULL 触发 NOT NULL 约束失败。因此在「测试基础设施」层
# 将 BigInteger 在 sqlite 方言下编译为 INTEGER，使内存库可正确验证仓储层行为；
# 此适配仅作用于测试，不改变生产模型定义与 MySQL 行为。
from sqlalchemy import BigInteger  # noqa: E402
from sqlalchemy.ext.compiler import compiles  # noqa: E402


@compiles(BigInteger, "sqlite")
def _compile_big_integer_sqlite(type_, compiler, **kw):  # pragma: no cover - 测试基础设施
    """在 SQLite 方言下将 BigInteger 渲染为 INTEGER，以支持自增主键。"""
    return "INTEGER"
