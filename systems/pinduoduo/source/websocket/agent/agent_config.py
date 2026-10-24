# -*- coding: utf-8 -*-
"""
websocket.agent.agent_config —— AI 回复引擎配置
================================================
本文件用途：复用改造 Customer-Agent 的 ``AgentConfig``，定义 websocket 服务
AI 回复引擎的运行时配置（模型名称、API 密钥、API 地址、指令、温度、最大循环
轮数、AI 开关），并提供从持久化的 ``LlmConfig`` 模型构建配置的方法。

关键约束（开发规范 + 需求 8.6）：
- API 密钥经 ``LlmConfig.api_key_enc`` 加密存储，本模块通过
  ``common.utils.crypto.try_decrypt_text`` 解密为运行时明文使用，绝不在日志 /
  对外响应中回显明文（脱敏由 backend 序列化层负责）。
- API 地址、模型名称等不写死，统一从配置 / 数据库读取（需求 8.6、规范 21）。
- 指令以 JSON 文本存储于 ``LlmConfig.instructions``，本模块负责解析为列表。

与参照项目差异：
- 参照项目 ``AgentConfig`` 从本地文件配置加载（``get_config``）；本系统改造为
  从数据库 ``LlmConfig`` 记录构建，契合多服务 + MySQL 架构。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, List, Optional

from common.services.ai_provider_service import normalize_ai_provider_type
from common.utils.crypto import try_decrypt_text

# 默认参数（与参照项目口径一致，必要处按本系统调整）。
DEFAULT_MAX_LOOPS: int = 5  # Agent 循环最大轮数（防止无限工具调用）
DEFAULT_TEMPERATURE: float = 0.3  # LLM 温度（越低越确定）
DEFAULT_MODEL_NAME: str = "gpt-3.5-turbo"  # 缺省模型名（实际应由数据库配置）
DEFAULT_TIMEOUT_SECONDS: float = 60.0  # 单次 LLM 调用超时（秒），超时回退默认回复
DEFAULT_PROVIDER_TYPE: str = "openai_compatible"  # 缺省接口类型（OpenAI 兼容）


@dataclass
class AgentConfig:
    """AI 回复引擎配置数据类。

    Attributes:
        model_name: LLM 模型名称。
        api_key: LLM API 密钥明文（由密文解密得到，仅运行时驻留内存）。
        api_base: LLM API 接口地址。
        provider_type: 接口类型 / 服务商协议（openai_compatible / anthropic /
            gemini / dashscope_app），决定实际调用走哪种协议（需求 8）。
        instructions: 提示词指令列表（追加到系统 Prompt）。
        temperature: 采样温度。
        max_loops: Agent 循环最大轮数。
        timeout_seconds: 单次 LLM 调用超时秒数。
        ai_enabled: 是否启用 AI 回复（需求 8.1）。
    """

    model_name: str = DEFAULT_MODEL_NAME
    api_key: str = ""
    api_base: str = ""
    provider_type: str = DEFAULT_PROVIDER_TYPE
    instructions: List[str] = field(default_factory=list)
    temperature: float = DEFAULT_TEMPERATURE
    max_loops: int = DEFAULT_MAX_LOOPS
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    ai_enabled: bool = False

    def validate(self) -> bool:
        """校验配置是否可用于发起 LLM 调用。

        Returns:
            True 表示配置有效（已启用且 API 密钥非空）；否则 False。
        """
        if not self.ai_enabled:
            return False
        return bool(self.api_key)

    @classmethod
    def from_llm_config(cls, llm_config: Any) -> "AgentConfig":
        """从持久化的 LlmConfig 模型（或等价 dict）构建运行时配置。

        - API 密钥从 ``api_key_enc`` 密文解密为明文（解密失败按空串处理，
          后续 validate 会判定不可用，避免以坏密钥发起调用）；
        - 指令从 JSON 文本解析为字符串列表（解析失败按空列表处理）。

        Args:
            llm_config: LlmConfig 模型实例或包含同名字段的字典。

        Returns:
            构建好的 AgentConfig 实例。
        """
        get = _attr_getter(llm_config)

        api_key_enc = get("api_key_enc")
        api_key = try_decrypt_text(api_key_enc) or ""

        instructions = _parse_instructions(get("instructions"))

        # 接口类型经统一规范化（兼容历史无此字段 / 别名的配置）。
        provider_type = normalize_ai_provider_type(
            get("provider_type"), get("api_base"), get("model_name")
        )

        return cls(
            model_name=get("model_name") or DEFAULT_MODEL_NAME,
            api_key=api_key,
            api_base=get("api_base") or "",
            provider_type=provider_type,
            instructions=instructions,
            temperature=DEFAULT_TEMPERATURE,
            max_loops=DEFAULT_MAX_LOOPS,
            timeout_seconds=DEFAULT_TIMEOUT_SECONDS,
            ai_enabled=bool(get("ai_enabled")),
        )


def _attr_getter(source: Any):
    """返回一个从模型对象或字典统一取值的函数。"""

    if isinstance(source, dict):
        return lambda key, default=None: source.get(key, default)
    return lambda key, default=None: getattr(source, key, default)


def _parse_instructions(raw: Optional[str]) -> List[str]:
    """将 JSON 文本形式的指令解析为字符串列表。

    兼容三种情况：None / 空 -> 空列表；JSON 数组 -> 元素转字符串列表；
    其它非法 JSON -> 当作单条纯文本指令。

    Args:
        raw: 指令原始文本（可能为 None / JSON 数组 / 纯文本）。

    Returns:
        指令字符串列表。
    """
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        # 非 JSON：视为单条纯文本指令
        return [str(raw)]
    if isinstance(parsed, list):
        return [str(item) for item in parsed if str(item).strip()]
    if isinstance(parsed, str) and parsed.strip():
        return [parsed]
    return []


__all__ = [
    "AgentConfig",
    "DEFAULT_MAX_LOOPS",
    "DEFAULT_TEMPERATURE",
    "DEFAULT_MODEL_NAME",
    "DEFAULT_TIMEOUT_SECONDS",
]
