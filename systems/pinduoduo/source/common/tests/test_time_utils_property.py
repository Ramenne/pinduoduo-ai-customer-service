# -*- coding: utf-8 -*-
"""
北京时间转换属性测试（common/tests/test_time_utils_property.py）
================================================================
本文件用途：以 Hypothesis 属性测试验证 common.utils.time_utils 中
北京时间与 UTC 互转函数（utc_to_beijing / beijing_to_utc）满足设计文档
Property 23「北京时间转换可逆且偏移固定」。

说明：
- 中国大陆自 1991 年起不再实行夏令时，Asia/Shanghai 相对 UTC 偏移恒为
  固定的 +8 小时；为避免历史夏令时（1986~1991）干扰「偏移恒为 +8」的断言，
  生成的 UTC 时间统一限制在 1992-01-01 之后。
- 每个属性测试最少迭代 100 次（max_examples=200，满足规范要求）。
"""
from datetime import datetime, timedelta, timezone

from hypothesis import given, settings
from hypothesis import strategies as st

from common.utils.time_utils import (
    UTC_TZ,
    beijing_to_utc,
    utc_to_beijing,
)

# 生成带 UTC 时区、且位于 1992 年之后的随机时间（规避历史夏令时影响）。
_utc_datetimes = st.datetimes(
    min_value=datetime(1992, 1, 1),
    max_value=datetime(2100, 12, 31, 23, 59, 59),
    timezones=st.just(timezone.utc),
)


@settings(max_examples=200)
@given(dt=_utc_datetimes)
def test_property_23_beijing_conversion_reversible_and_fixed_offset(dt: datetime):
    # Feature: pdd-auto-reply, Property 23: 北京时间转换可逆且偏移固定
    # Validates: Requirements 24.8

    beijing = utc_to_beijing(dt)

    # 断言一：偏移固定——北京时间的挂钟时刻恒等于 UTC 挂钟时刻 + 8 小时。
    expected_wall = dt.replace(tzinfo=None) + timedelta(hours=8)
    assert beijing.replace(tzinfo=None) == expected_wall
    # 北京时间相对 UTC 的偏移恒为 +8 小时。
    assert beijing.utcoffset() == timedelta(hours=8)

    # 断言二：往返可逆——北京时间再转回 UTC 应还原为同一时刻（时区归一化比较）。
    back_to_utc = beijing_to_utc(beijing)
    assert back_to_utc.tzinfo is not None
    # 比较绝对时刻（astimezone 到 UTC 后逐字段一致即同一时刻）。
    assert back_to_utc.astimezone(UTC_TZ) == dt.astimezone(UTC_TZ)
    # 时刻戳一致，进一步确认往返无精度丢失。
    assert back_to_utc.timestamp() == dt.timestamp()
