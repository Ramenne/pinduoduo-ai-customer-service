# -*- coding: utf-8 -*-
"""
common.services.service_client —— 统一的服务间 HTTP 客户端封装
============================================================
本文件用途：为「拼多多自动回复」系统各服务（backend / websocket / scheduler）
提供**统一的服务间 HTTP 调用客户端**，替换并统一原先分散在各服务内的 urllib
客户端（backend 的 connection_notify / product_sync_client / chat_send_client、
scheduler 的 service_client），满足需求 25.1 / 25.4 与规范 36（同一能力不重复实现）、
规范 52（新增方法先复用共通）。

设计要点：
- **地址经环境变量配置，禁止写死 localhost**（规范 21 / 需求 25.4）：目标服务基础
  地址统一取自 common 配置 ``websocket_service_url`` / ``backend_web_service_url`` /
  ``scheduler_service_url``（环境变量优先，缺省回退 Docker 服务名）。
- **仅依赖标准库 urllib**：避免为 backend / scheduler 引入额外 HTTP 客户端依赖，
  与原分散客户端口径保持一致，便于平滑迁移。
- **健壮性兜底**：网络不可达 / 超时 / 非 2xx / 响应解析失败一律被捕获，规整为
  ``ServiceResponse(ok=False, ...)`` 返回，不向上抛异常打断调用方主流程（需求 26）。
- **统一响应体解析**：各服务对外恒返回 ``{code, success, message, data}``（规范 1-3），
  本客户端据此提供 ``success`` / ``message`` / ``data`` 便捷访问，供调用方按业务语义判定。

实现约束（开发规范）：导入置顶（规范 51）、中文注释完善（规范 37）、文件名用下划线
（规范 40）、单文件 ≤500 行（规范 35）。
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional

from common.core.config import get_settings

# 模块级日志记录器（统一 info/warning/error，禁用 debug —— 规范 38）。
logger = logging.getLogger("common.service_client")

# 服务间调用默认超时（秒）：兼顾外部依赖耗时与有界等待，调用方可按需覆盖。
DEFAULT_TIMEOUT_SECONDS: float = 30.0

# 默认请求头：服务间统一以 JSON 交互。
_JSON_HEADERS: Dict[str, str] = {"Content-Type": "application/json"}


@dataclass
class ServiceResponse:
    """服务间 HTTP 调用结果（对目标服务统一响应体的规整）。

    区分「传输层成败」与「业务层成败」两个维度，供调用方按需取用：
    - ``ok``：传输层是否成功（HTTP 2xx 且无网络 / 解析异常）。
    - ``success``（属性）：业务层是否成功（响应体 ``success`` 字段为真）。

    Attributes:
        ok: 传输层是否成功（2xx 且响应体成功解析为 JSON）。
        status: HTTP 状态码（无响应 / 网络异常时为 None）。
        body: 解析后的响应体字典（解析失败 / 非字典时为 None）。
        error: 失败原因（中文）；传输层成功时为空字符串。
    """

    ok: bool = False
    status: Optional[int] = None
    body: Optional[Dict[str, Any]] = None
    error: str = ""

    @property
    def success(self) -> bool:
        """业务层是否成功：响应体存在且 ``success`` 字段为真。"""
        return bool(self.body) and bool(self.body.get("success"))

    @property
    def message(self) -> str:
        """响应体中的中文提示信息（无则为空字符串）。"""
        if not self.body:
            return self.error
        return str(self.body.get("message") or "")

    @property
    def data(self) -> Optional[Any]:
        """响应体中的业务数据（字典或列表均原样返回，其它类型为 None）。

        统一响应体的 ``data`` 既可能是对象（dict），也可能是数组（list，如直接
        返回列表的接口）。此前仅返回 dict 会把列表型数据丢弃为 None，导致调用方
        拿不到数据；这里对 dict / list 均原样返回。
        """
        if not self.body:
            return None
        value = self.body.get("data")
        return value if isinstance(value, (dict, list)) else None


# ----------------------------------------------------------------------
# 目标服务基础地址（环境变量优先，禁止写死 localhost —— 规范 21 / 需求 25.4）
# ----------------------------------------------------------------------
def websocket_base_url() -> str:
    """返回 websocket 服务基础地址（环境变量 ``WEBSOCKET_SERVICE_URL``）。"""
    return get_settings().websocket_service_url


def backend_base_url() -> str:
    """返回 backend 服务基础地址（环境变量 ``BACKEND_WEB_SERVICE_URL``）。"""
    return get_settings().backend_web_service_url


def scheduler_base_url() -> str:
    """返回 scheduler 服务基础地址（环境变量 ``SCHEDULER_SERVICE_URL``）。"""
    return get_settings().scheduler_service_url


# ----------------------------------------------------------------------
# 通用请求执行
# ----------------------------------------------------------------------
def _request(
    method: str,
    url: str,
    *,
    data: Optional[bytes] = None,
    headers: Optional[Mapping[str, str]] = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> ServiceResponse:
    """执行一次 HTTP 请求并将结果规整为 ``ServiceResponse``（健壮性兜底）。

    任何网络 / 解析异常均被捕获并转为 ``ok=False``，不向上抛出，确保不打断调用方
    主流程（需求 26）。

    Args:
        method: HTTP 方法（GET / POST 等）。
        url: 完整请求地址。
        data: 请求体字节（GET 通常为 None）。
        headers: 请求头。
        timeout: 超时时间（秒）。

    Returns:
        规整后的 ``ServiceResponse``。
    """
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers=dict(headers or {}),
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            status = int(getattr(resp, "status", resp.getcode()))
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        # 非 2xx：记录状态码与原因，视为传输层失败（不抛出）。
        logger.warning("服务间调用返回错误状态：url=%s status=%s", url, exc.code)
        return ServiceResponse(ok=False, status=exc.code, error=f"目标服务返回状态 {exc.code}")
    except (urllib.error.URLError, OSError) as exc:
        # 网络不可达 / 超时：视为外部依赖暂不可用（不抛出）。
        logger.warning("服务间调用失败：url=%s err=%s", url, exc)
        return ServiceResponse(ok=False, error=f"目标服务暂不可用：{exc}")

    # 传输层成功：尝试解析 JSON 响应体（统一响应体 {code, success, message, data}）。
    body: Optional[Dict[str, Any]] = None
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                body = parsed
            else:
                logger.warning("服务间调用响应体非字典：url=%s", url)
        except ValueError as exc:
            logger.warning("服务间调用响应解析失败：url=%s err=%s", url, exc)
            return ServiceResponse(
                ok=False, status=status, error="目标服务返回格式异常"
            )
    return ServiceResponse(ok=True, status=status, body=body)


def _build_url(base_url: str, path: str, params: Optional[Mapping[str, Any]] = None) -> str:
    """拼接目标服务的完整请求地址（基础地址去除尾部斜杠 + 相对路径）。

    Args:
        base_url: 目标服务基础地址（经环境变量配置，禁止写死 localhost）。
        path: 接口相对路径（以 / 开头）。
        params: 可选查询参数（用于 GET）。

    Returns:
        完整请求地址。
    """
    url = f"{base_url.rstrip('/')}{path}"
    if params:
        query = urllib.parse.urlencode(
            {k: v for k, v in params.items() if v is not None}
        )
        if query:
            url = f"{url}?{query}"
    return url


def post_json(
    base_url: str,
    path: str,
    payload: Optional[Mapping[str, Any]] = None,
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    headers: Optional[Mapping[str, str]] = None,
) -> ServiceResponse:
    """向目标服务发起 POST(JSON) 调用（统一入口）。

    Args:
        base_url: 目标服务基础地址（经环境变量配置）。
        path: 接口相对路径（以 / 开头）。
        payload: 请求体（将序列化为 JSON）。
        timeout: 超时时间（秒）。
        headers: 额外请求头（与默认 JSON 头合并，可用于服务间内部密钥等）。

    Returns:
        规整后的 ``ServiceResponse``。
    """
    body = json.dumps(payload or {}).encode("utf-8")
    url = _build_url(base_url, path)
    merged_headers = dict(_JSON_HEADERS)
    if headers:
        merged_headers.update(headers)
    return _request("POST", url, data=body, headers=merged_headers, timeout=timeout)


def get_json(
    base_url: str,
    path: str,
    params: Optional[Mapping[str, Any]] = None,
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> ServiceResponse:
    """向目标服务发起 GET 调用（统一入口）。

    Args:
        base_url: 目标服务基础地址（经环境变量配置）。
        path: 接口相对路径（以 / 开头）。
        params: 查询参数。
        timeout: 超时时间（秒）。

    Returns:
        规整后的 ``ServiceResponse``。
    """
    url = _build_url(base_url, path, params)
    return _request("GET", url, headers=_JSON_HEADERS, timeout=timeout)


__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "ServiceResponse",
    "websocket_base_url",
    "backend_base_url",
    "scheduler_base_url",
    "post_json",
    "get_json",
]


def backend_api_path(path: str) -> str:
    """供跨服务回调使用，与backend挂载的实际前缀一致。"""
    return os.getenv('BACKEND_WEB_API_PREFIX', '/api/v1').rstrip('/') + '/' + path.lstrip('/')
