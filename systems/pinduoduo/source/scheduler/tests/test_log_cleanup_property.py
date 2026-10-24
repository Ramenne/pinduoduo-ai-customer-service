# -*- coding: utf-8 -*-
"""
scheduler.tests.test_log_cleanup_property —— 文件日志清理属性测试
================================================================
Feature: pdd-auto-reply, 文件日志清理保留期不变式（需求 21.4 / 19.5）

本文件用属性测试（Hypothesis，最少 100 次迭代）验证文件日志清理的核心不变式：
对任意一组「修改时间各异的磁盘日志文件」与任意合法保留天数，``cleanup_log_files``
满足：
1. 修改时间早于「现在 - 保留天数」的日志文件被删除；
2. 修改时间不早于该阈值的日志文件被保留；
3. 非日志文件（无 .log 标识）一律不被删除；
4. 返回的「已删除列表」恰好等于「应删除的过期日志文件集合」。

该不变式保证「按保留天数仅清理磁盘日志文件」的行为正确，且不会误删保留期内
或非日志文件（与「数据库业务日志禁止物理删除」由其它单元测试覆盖互补）。

注意：本测试不涉及数据库，纯磁盘文件操作 + 时间阈值判定。
"""
from __future__ import annotations

import os
from datetime import timedelta

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from common.utils.time_utils import now_beijing
from tasks import log_cleanup


# 每个候选文件：(文件名前缀, 是否日志文件, 相对现在的天数偏移)
# 天数偏移为正表示「过去多少天」，用于设置 mtime；范围覆盖远早于与晚于阈值。
_file_strategy = st.tuples(
    st.integers(min_value=0, max_value=999),          # 唯一编号，避免重名
    st.booleans(),                                    # 是否为日志文件
    st.integers(min_value=0, max_value=720),          # mtime 距今天数（0~约2年）
)


@settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    files=st.lists(_file_strategy, min_size=0, max_size=12),
    retention_days=st.integers(min_value=1, max_value=365),
)
def test_cleanup_removes_exactly_expired_log_files(tmp_path_factory, files, retention_days):
    """**Validates: Requirements 21.4, 19.5**

    清理结果恰好等于「过期日志文件」集合：过期日志删除、保留期内日志保留、
    非日志文件一律保留。
    """
    # 为每个样例使用独立临时目录，避免跨迭代相互影响。
    log_dir = tmp_path_factory.mktemp("logs")
    os.environ["LOG_FILE_DIR"] = str(log_dir)

    now = now_beijing()
    cutoff_ts = (now - timedelta(days=retention_days)).timestamp()

    expected_removed: set[str] = set()
    expected_kept: set[str] = set()

    for index, (number, is_log, day_offset) in enumerate(files):
        # 文件名：日志文件用 .log 后缀，非日志文件用 .txt。
        suffix = ".log" if is_log else ".txt"
        path = log_dir / f"f{index}_{number}{suffix}"
        path.write_text("data", encoding="utf-8")
        # 设置 mtime 为「距今 day_offset 天 + 12 小时」。额外的 12 小时使文件
        # 修改时间永不落在「整数天」的阈值附近（实现侧 now 比测试 now 略晚数毫秒，
        # 半天的间隔足以消除该边界抖动），保证分类稳定可预测。
        mtime = (now - timedelta(days=day_offset, hours=12)).timestamp()
        os.utime(path, (mtime, mtime))

        # 预期：仅日志文件且 mtime 早于阈值才会被删除。
        if is_log and mtime < cutoff_ts:
            expected_removed.add(str(path))
        else:
            expected_kept.add(str(path))

    removed = set(log_cleanup.cleanup_log_files(retention_days=retention_days))

    # 1) 返回的已删除集合恰好等于预期过期日志集合。
    assert removed == expected_removed
    # 2) 过期日志文件确已从磁盘删除。
    for p in expected_removed:
        assert not os.path.exists(p)
    # 3) 保留期内日志与非日志文件均保留。
    for p in expected_kept:
        assert os.path.exists(p)
