# -*- coding: utf-8 -*-
"""
websocket.tests.test_business_hours_property —— 营业时间判定属性测试
====================================================================
本文件用途：以 Hypothesis 属性测试验证 websocket.engine.business_hours
营业时间判定纯逻辑的正确性，对应设计文档 Property 17（营业时间判定正确），
覆盖需求 11.2 / 11.3 / 11.4：

- 11.2：当前北京时间处于营业区间内 → 判定为营业中（返回 True）；
- 11.3：当前北京时间不处于营业区间内 → 判定为非营业（返回 False）；
- 11.4：营业时间未配置（或控制关闭）→ 默认全天营业（恒为 True）。

测试策略：
- 用独立于被测实现的「参考判定」复算预期结果，覆盖普通区间与跨午夜区间；
- 时刻入参以 datetime.time 生成，参考时刻以带北京时区的 datetime 生成，
  确保全链路口径为北京时间（Asia/Shanghai, UTC+8）。

测试框架：pytest + Hypothesis（最少 100 次迭代）。
"""
from datetime import datetime, time

from hypothesis import given, settings
from hypothesis import strategies as st

from common.utils.time_utils import BEIJING_TZ
from engine.business_hours import is_within_business_hours

# 时刻生成器：覆盖 00:00:00 ~ 23:59:59 全天范围（秒级精度）。
_time_strategy = st.times()

# 参考时刻生成器：生成带北京时区的 datetime，保证判定口径为北京时间。
_beijing_datetime_strategy = st.datetimes(
    min_value=datetime(2020, 1, 1, 0, 0, 0),
    max_value=datetime(2030, 12, 31, 23, 59, 59),
    timezones=st.just(BEIJING_TZ),
)


def _expected_within(start: time, end: time, current: time) -> bool:
    """参考判定：独立复算「current 是否处于 [start, end) 营业区间」。

    与被测实现保持同一语义但独立实现，用作属性测试的预期对照：
    - start == end：视为覆盖 24 小时的全天营业，恒为 True；
    - start < end：普通区间，start <= current < end 为营业中；
    - start > end：跨午夜区间，current >= start 或 current < end 为营业中。
    """
    if start == end:
        return True
    if start < end:
        return start <= current < end
    return current >= start or current < end


@settings(max_examples=200)
@given(
    start=_time_strategy,
    end=_time_strategy,
    reference=_beijing_datetime_strategy,
)
def test_business_hours_within_interval(start, end, reference):
    """Feature: pdd-auto-reply, Property 17: 营业时间判定正确

    对任意营业起止时刻与任意北京时间参考时刻：营业时间判定结果必须与独立
    参考判定一致（需求 11.2 营业内为 True / 11.3 营业外为 False），覆盖普通
    区间与跨午夜区间。
    """
    result = is_within_business_hours(start, end, now=reference)
    expected = _expected_within(start, end, reference.time())
    assert result is expected


@settings(max_examples=100)
@given(
    start=st.one_of(st.none(), _time_strategy),
    end=st.one_of(st.none(), _time_strategy),
    reference=_beijing_datetime_strategy,
)
def test_business_hours_unconfigured_defaults_open(start, end, reference):
    """Feature: pdd-auto-reply, Property 17: 营业时间判定正确

    需求 11.4：营业起止任一未配置（None）时默认全天营业，判定恒为 True；
    起止均配置时，与独立参考判定一致。
    """
    result = is_within_business_hours(start, end, now=reference)
    if start is None or end is None:
        assert result is True
    else:
        assert result is _expected_within(start, end, reference.time())


@settings(max_examples=100)
@given(
    start=_time_strategy,
    end=_time_strategy,
    reference=_beijing_datetime_strategy,
)
def test_business_hours_disabled_defaults_open(start, end, reference):
    """Feature: pdd-auto-reply, Property 17: 营业时间判定正确

    需求 11.4：营业时间控制关闭（enabled=False）时，无论起止时刻与当前时刻
    如何，均默认全天营业，判定恒为 True。
    """
    result = is_within_business_hours(start, end, enabled=False, now=reference)
    assert result is True
