# -*- coding: utf-8 -*-
"""
common.utils.pagination —— 分页响应结构与分页参数规整
=====================================================
本文件用途：定义「拼多多自动回复」系统统一的后端分页结构与分页参数规整逻辑。

依据《开发规范》第 28 条与需求 3.3：
- 所有查询界面采用后端分页，分页响应结构统一为 {list, total, page, page_size}。
- 默认每页 ``DEFAULT_PAGE_SIZE``(20) 条，每页大小可选值为 10 / 20 / 50 / 100。
- 非法的每页大小默认回退为 ``DEFAULT_PAGE_SIZE``(20)（宽松模式）；如业务需要
  严格校验，可调用 ``normalize_page_size(..., strict=True)`` 抛出异常。
- 页码从 1 开始，非法页码（<1 或非整数）规整为 1。

本模块仅构造纯数据结构（pydantic 模型），便于序列化为 JSON 放入统一响应体的
``data`` 字段，不涉及数据库访问（数据库分页查询在仓储层任务中实现）。
"""
from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

# 默认每页条数（规范 28）。
DEFAULT_PAGE_SIZE: int = 20

# 允许的每页条数可选值（规范 28）。
ALLOWED_PAGE_SIZES: tuple[int, ...] = (10, 20, 50, 100)

# 默认页码，从 1 开始。
DEFAULT_PAGE: int = 1

# 分页列表项的泛型类型变量。
T = TypeVar("T")


def normalize_page(page: Any) -> int:
    """规整页码，保证返回从 1 开始的合法整数页码。

    页码非整数、小于 1 或无法转换时一律回退为 ``DEFAULT_PAGE``(1)，避免出现
    负偏移或非法分页。

    Args:
        page: 待规整的页码（可能来自请求参数，类型不可信）。

    Returns:
        合法页码（>= 1）。
    """
    try:
        # bool 是 int 的子类，需排除，避免 True/False 被当作页码。
        if isinstance(page, bool):
            return DEFAULT_PAGE
        page_int = int(page)
    except (TypeError, ValueError):
        return DEFAULT_PAGE
    return page_int if page_int >= 1 else DEFAULT_PAGE


def normalize_page_size(page_size: Any, strict: bool = False) -> int:
    """规整每页条数，保证返回值落在允许的可选值集合内。

    校验规则：
    - 合法值（10/20/50/100）原样返回。
    - 非法值在宽松模式（默认）下回退为 ``DEFAULT_PAGE_SIZE``(20)。
    - 非法值在严格模式（``strict=True``）下抛出 ``ValueError``，供需要明确报错
      的接口使用（按设计「非法值回退默认或报错按设计」）。

    Args:
        page_size: 待规整的每页条数（类型不可信）。
        strict: 是否启用严格校验；为 True 时非法值抛出 ``ValueError``。

    Returns:
        合法的每页条数（属于 ``ALLOWED_PAGE_SIZES``）。

    Raises:
        ValueError: 严格模式下传入非法每页条数时抛出。
    """
    try:
        # 排除 bool，避免 True/False 被当作条数。
        if isinstance(page_size, bool):
            raise ValueError("分页大小不能为布尔值")
        size_int = int(page_size)
    except (TypeError, ValueError):
        if strict:
            raise ValueError(
                f"非法的每页条数，可选值为 {list(ALLOWED_PAGE_SIZES)}"
            )
        return DEFAULT_PAGE_SIZE

    if size_int in ALLOWED_PAGE_SIZES:
        return size_int
    if strict:
        raise ValueError(
            f"非法的每页条数 {size_int}，可选值为 {list(ALLOWED_PAGE_SIZES)}"
        )
    return DEFAULT_PAGE_SIZE


def normalize_pagination(
    page: Any = DEFAULT_PAGE,
    page_size: Any = DEFAULT_PAGE_SIZE,
    strict: bool = False,
) -> tuple[int, int]:
    """同时规整页码与每页条数。

    Args:
        page: 待规整的页码。
        page_size: 待规整的每页条数。
        strict: 每页条数是否严格校验（详见 ``normalize_page_size``）。

    Returns:
        ``(page, page_size)`` 规整后的二元组。
    """
    return normalize_page(page), normalize_page_size(page_size, strict=strict)


def calc_offset(page: int, page_size: int) -> int:
    """根据页码与每页条数计算查询偏移量（offset）。

    Args:
        page: 页码（从 1 开始）。
        page_size: 每页条数。

    Returns:
        数据库查询使用的偏移量，恒 >= 0。
    """
    return max(int(page) - 1, 0) * int(page_size)


class PageResult(BaseModel, Generic[T]):
    """统一分页响应结构 {list, total, page, page_size}。

    字段说明：
    - list: 当前页数据项列表（已序列化的业务数据）。
    - total: 满足条件的总记录数。
    - page: 当前页码（从 1 开始）。
    - page_size: 每页条数（属于 ``ALLOWED_PAGE_SIZES``）。

    该结构通常作为统一响应体 ``ApiResponse.data`` 的内容返回给前端。
    使用别名 ``list`` 映射到内部字段 ``items``，避免与 Python 内置 ``list`` 冲突。
    """

    # 内部字段名用 items，对外序列化为 "list"（规范要求的字段名）。
    items: list[T] = Field(default_factory=list, alias="list")
    total: int = 0
    page: int = DEFAULT_PAGE
    page_size: int = DEFAULT_PAGE_SIZE

    # 允许通过字段名或别名构造，并在序列化时输出别名 "list"。
    model_config = ConfigDict(populate_by_name=True)


def build_page_result(
    items: list[Any],
    total: int,
    page: Any = DEFAULT_PAGE,
    page_size: Any = DEFAULT_PAGE_SIZE,
    strict: bool = False,
) -> PageResult:
    """构造统一分页响应结构。

    内部会对 ``page`` 与 ``page_size`` 进行规整，确保返回结构中的分页参数始终
    合法（页码 >= 1、每页条数属于允许集合）。

    Args:
        items: 当前页的数据项列表。
        total: 总记录数。
        page: 当前页码（将被规整）。
        page_size: 每页条数（将被规整）。
        strict: 每页条数是否严格校验。

    Returns:
        构造完成的 ``PageResult`` 实例。
    """
    norm_page, norm_size = normalize_pagination(page, page_size, strict=strict)
    return PageResult(
        items=list(items),
        total=int(total) if total and int(total) > 0 else 0,
        page=norm_page,
        page_size=norm_size,
    )


def build_pagination_dict(
    items: list[Any],
    total: int,
    page: Any = DEFAULT_PAGE,
    page_size: Any = DEFAULT_PAGE_SIZE,
    strict: bool = False,
) -> dict[str, Any]:
    """构造统一分页响应字典 {list, total, page, page_size}。

    与 ``build_page_result`` 等价，但直接返回字典，便于无需 pydantic 模型的
    场景直接放入响应体 ``data``。

    Args:
        items: 当前页的数据项列表。
        total: 总记录数。
        page: 当前页码（将被规整）。
        page_size: 每页条数（将被规整）。
        strict: 每页条数是否严格校验。

    Returns:
        ``{"list": [...], "total": N, "page": P, "page_size": S}`` 字典。
    """
    result = build_page_result(items, total, page, page_size, strict=strict)
    return result.model_dump(by_alias=True)


__all__ = [
    "DEFAULT_PAGE_SIZE",
    "ALLOWED_PAGE_SIZES",
    "DEFAULT_PAGE",
    "normalize_page",
    "normalize_page_size",
    "normalize_pagination",
    "calc_offset",
    "PageResult",
    "build_page_result",
    "build_pagination_dict",
]
