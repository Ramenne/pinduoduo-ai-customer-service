# -*- coding: utf-8 -*-
"""
test_ai_reply_engine —— AI 回复引擎与工具调用单元测试
======================================================
本文件用途：验证 agent.ai_reply_engine 的 Agent 循环与失败降级逻辑（需求 8）：
- 配置不可用 / 未启用时直接回退默认回复，记「AI 回复失败」；
- LLM 调用异常 / 超时 / 返回空内容时回退默认回复，记「AI 回复失败」（需求 8.5）；
- 正常返回文本时输出 AI 回复（中文），记「AI回复」；
- 触发工具调用时经知识库工具检索后再生成回复（需求 8.2/8.3/8.4），并验证
  店铺隔离参数注入与工具名记录；
- llm_client / tools 的纯逻辑辅助函数行为。

测试不依赖真实 LLM 与数据库：通过注入「假 LLM 客户端」与对 tools.execute_tool
打桩，验证 Agent 循环编排逻辑。
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List

import pytest

from agent import ai_reply_engine, tools
from agent.agent_config import AgentConfig
from agent.ai_reply_engine import (
    RESULT_AI_REPLY,
    RESULT_AI_REPLY_FAILED,
    AIReplyResult,
    generate_reply,
)
from agent.llm_client import LLMResponse, ToolCall, ToolFunction


@pytest.fixture(autouse=True)
def catalog_fixture(monkeypatch):
    monkeypatch.setattr(ai_reply_engine, 'list_shop_products', lambda params: '【本店已同步商品目录】测试商品；实时库存未知。')


# ----------------------------------------------------------------------
# 测试辅助：可编排的假 LLM 客户端
# ----------------------------------------------------------------------
class FakeLLMClient:
    """假 LLM 客户端：按预设响应序列依次返回，记录收到的 messages。"""

    def __init__(self, responses: List[Any]) -> None:
        # responses 元素可为 LLMResponse 或 Exception（抛出）
        self._responses = list(responses)
        self.tools: List[Dict[str, Any]] = []
        self.calls: List[List[Dict[str, Any]]] = []

    async def chat(self, messages, tool_choice: str = "auto") -> LLMResponse:
        # 记录消息快照便于断言
        self.calls.append(list(messages))
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _enabled_config() -> AgentConfig:
    """构造一个「已启用且密钥非空」的可用配置。"""
    return AgentConfig(
        model_name="test-model",
        api_key="sk-test",
        api_base="https://example.com/v1",
        instructions=["请优先推荐高性价比商品。"],
        temperature=0.3,
        max_loops=3,
        timeout_seconds=5.0,
        ai_enabled=True,
    )


def _run(coro):
    return asyncio.run(coro)


# ----------------------------------------------------------------------
# 配置不可用 / 未启用 -> 回退默认回复
# ----------------------------------------------------------------------
def test_config_disabled_falls_back_to_default():
    """AI 未启用时直接回退默认回复并记 ai_reply_failed。"""
    config = AgentConfig(ai_enabled=False, api_key="sk-test")
    result = _run(
        generate_reply("你好", config, shop_id=1, default_reply="默认回复内容")
    )
    assert isinstance(result, AIReplyResult)
    assert result.success is False
    assert result.used_default is True
    assert result.content == "默认回复内容"
    assert result.log_result == RESULT_AI_REPLY_FAILED


def test_config_missing_key_falls_back():
    """启用但缺少密钥时回退默认回复。"""
    config = AgentConfig(ai_enabled=True, api_key="")
    result = _run(generate_reply("你好", config, default_reply="兜底"))
    assert result.success is False
    assert result.log_result == RESULT_AI_REPLY_FAILED
    assert result.content == "兜底"


# ----------------------------------------------------------------------
# 正常返回文本 -> AI 回复成功
# ----------------------------------------------------------------------
def test_direct_text_reply_success():
    """LLM 直接返回文本时输出 AI 回复，记 ai_reply。"""
    client = FakeLLMClient([LLMResponse(content="您好，很高兴为您服务。")])
    result = _run(
        generate_reply("在吗", _enabled_config(), shop_id=2, default_reply="默认", client=client)
    )
    assert result.success is True
    assert result.content == "您好，很高兴为您服务。"
    assert result.log_result == RESULT_AI_REPLY
    assert result.used_default is False
    # 系统 Prompt 应包含店铺自定义指令
    system_msg = client.calls[0][0]
    assert system_msg["role"] == "system"
    assert "高性价比" in system_msg["content"]
    assert "当前店铺内部ID为 2" in system_msg["content"]


# ----------------------------------------------------------------------
# 工具调用 -> 检索后再回复（需求 8.2/8.3/8.4）
# ----------------------------------------------------------------------
def test_tool_call_then_reply(monkeypatch):
    """首轮请求工具调用，执行知识库工具后第二轮返回最终回复。"""
    tool_call = ToolCall(
        id="call_1",
        function=ToolFunction(
            name=tools.TOOL_SEARCH_CS_KNOWLEDGE,
            arguments='{"query": "退货", "shop_id": 999}',
        ),
    )
    client = FakeLLMClient(
        [
            LLMResponse(content="", tool_calls=[tool_call]),
            LLMResponse(content="我们支持7天无理由退货。"),
        ]
    )

    captured: Dict[str, Any] = {}

    def fake_execute_tool(name: str, params: Dict[str, Any]) -> str:
        captured["name"] = name
        captured["params"] = params
        return "【客服知识】7天无理由退货政策"

    monkeypatch.setattr(ai_reply_engine, "execute_tool", fake_execute_tool)

    result = _run(
        generate_reply("能退货吗", _enabled_config(), shop_id=7, default_reply="默认", client=client)
    )
    assert result.success is True
    assert result.content == "我们支持7天无理由退货。"
    assert result.tool_calls_made == [tools.TOOL_SEARCH_CS_KNOWLEDGE]
    # 店铺隔离参数应被注入并覆盖模型给出的 shop_id
    assert captured["params"]["shop_id"] == 7
    assert captured["name"] == tools.TOOL_SEARCH_CS_KNOWLEDGE
    # 第二轮消息中应包含 tool 角色的检索结果
    second_call_messages = client.calls[1]
    assert any(m.get("role") == "tool" for m in second_call_messages)


# ----------------------------------------------------------------------
# LLM 异常 / 空内容 -> 回退默认回复（需求 8.5）
# ----------------------------------------------------------------------
def test_llm_exception_falls_back():
    """LLM 调用抛异常时回退默认回复并记 ai_reply_failed。"""
    client = FakeLLMClient([RuntimeError("网络错误")])
    result = _run(
        generate_reply("你好", _enabled_config(), default_reply="默认回复", client=client)
    )
    assert result.success is False
    assert result.used_default is True
    assert result.content == "默认回复"
    assert result.log_result == RESULT_AI_REPLY_FAILED
    assert "网络错误" in (result.error or "")


def test_llm_empty_content_falls_back():
    """LLM 返回空内容时回退默认回复。"""
    client = FakeLLMClient([LLMResponse(content="   ")])
    result = _run(
        generate_reply("你好", _enabled_config(), default_reply="默认回复", client=client)
    )
    assert result.success is False
    assert result.log_result == RESULT_AI_REPLY_FAILED
    assert result.content == "默认回复"


def test_max_loops_forces_final_reply(monkeypatch):
    """持续请求工具调用时，达上限后强制基于已有信息给出最终回复。"""
    tool_call = ToolCall(
        id="c",
        function=ToolFunction(name=tools.TOOL_GET_PRODUCT_KNOWLEDGE, arguments='{"goods_id": "G1"}'),
    )
    config = _enabled_config()
    config.max_loops = 2
    # 第一轮工具调用，第二轮(最后一轮)仍工具调用 -> 触发强制收尾再请求一次
    client = FakeLLMClient(
        [
            LLMResponse(content="", tool_calls=[tool_call]),
            LLMResponse(content="", tool_calls=[tool_call]),
            LLMResponse(content="基于已有信息的最终回复。"),
        ]
    )
    monkeypatch.setattr(ai_reply_engine, "execute_tool", lambda n, p: "知识结果")
    result = _run(generate_reply("商品", config, shop_id=1, default_reply="默认", client=client))
    assert result.success is True
    assert result.content == "基于已有信息的最终回复。"


# ----------------------------------------------------------------------
# llm_client 纯逻辑
# ----------------------------------------------------------------------
def test_tool_function_parse_arguments():
    """ToolFunction 参数解析：合法 JSON / 非法 JSON / 空。"""
    assert ToolFunction("t", '{"a": 1}').parse_arguments() == {"a": 1}
    assert ToolFunction("t", "not-json").parse_arguments() == {}
    assert ToolFunction("t", "").parse_arguments() == {}
    # 非对象 JSON 也返回空字典
    assert ToolFunction("t", "[1,2]").parse_arguments() == {}


def test_llm_response_has_tool_calls():
    """LLMResponse.has_tool_calls 正确反映工具调用存在性。"""
    assert LLMResponse(content="x").has_tool_calls is False
    tc = ToolCall(id="1", function=ToolFunction("n"))
    assert LLMResponse(content="", tool_calls=[tc]).has_tool_calls is True


# ----------------------------------------------------------------------
# tools 纯逻辑：参数校验 + 结果格式化
# ----------------------------------------------------------------------
def test_get_product_knowledge_missing_params():
    """缺少 shop_id / goods_id 时返回中文错误提示，不触达数据库。"""
    assert "缺少店铺ID" in tools.get_product_knowledge({"goods_id": "G1"})
    assert "缺少商品ID" in tools.get_product_knowledge({"shop_id": 1})


def test_search_cs_knowledge_missing_params():
    """缺少 shop_id / query 时返回中文错误提示。"""
    assert "缺少店铺ID" in tools.search_customer_service_knowledge({"query": "退货"})
    assert "缺少搜索关键词" in tools.search_customer_service_knowledge({"shop_id": 1})


def test_execute_tool_unknown():
    """未知工具名返回中文错误提示。"""
    assert "未知工具" in tools.execute_tool("not_exist", {})


def test_format_search_result_empty():
    """空结果格式化为「未找到相关知识。」。"""
    assert tools.format_search_result(_empty_kb_result()) == "未找到相关知识。"


def test_format_search_result_with_data():
    """含商品/客服知识时格式化包含对应中文标题与内容。"""
    result = tools.format_search_result(_kb_result_with_data())
    assert "【产品知识】" in result
    assert "测试商品" in result
    assert "【客服知识】" in result
    assert "退货政策" in result


# ----------------------------------------------------------------------
# 测试辅助：构造 KbSearchResult（用最小对象模拟模型记录）
# ----------------------------------------------------------------------
class _Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _empty_kb_result():
    from common.services.kb_service import KbSearchResult

    return KbSearchResult(product_knowledge=[], customer_service_knowledge=[])


def _kb_result_with_data():
    from common.services.kb_service import KbSearchResult

    product = _Obj(goods_name="测试商品", goods_id="G1", price=9.9, extracted_content="规格说明")
    cs = _Obj(title="退货政策", content="支持7天无理由退货")
    return KbSearchResult(product_knowledge=[product], customer_service_knowledge=[cs])



def test_auto_reply_blocks_internal_diagnostics_and_uses_same_voice_rule():
    client=FakeLLMClient([LLMResponse(content='我的知识库没有查到保存方法。')])
    result=_run(generate_reply('怎么保存？',_enabled_config(),client=client,default_reply='知识库未找到，请稍后'))
    assert not result.success and result.error=='对客内容含内部处理说明' and result.content is None
    assert '自然、简洁的店铺客服口吻' in client.calls[0][0]['content']


def test_customer_business_word_is_not_blocked():
    client=FakeLLMClient([LLMResponse(content='这款数据库教程是电子版。')])
    result=_run(generate_reply('教程是纸质的吗？',_enabled_config(),client=client))
    assert result.success
