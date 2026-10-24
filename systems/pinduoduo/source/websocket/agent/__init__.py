"""
文件用途：AI 回复引擎包（agent）。

承载基于大语言模型的智能回复能力（后续任务实现）：
- Agent 循环编排（LLM → 解析 tool_calls → 执行工具 → 回传结果 → 循环，受最大轮数限制）；
- 工具集：商品知识检索、客服知识检索（经 common 的 kb.search）等；
- 基于检索内容生成中文回复；LLM 失败/超时回退默认回复并记「AI 回复失败」；
- 需发送商品卡片但签名不可用时，降级为文本回复商品信息。

已实现：
- agent_config：AI 回复引擎运行时配置（从持久化 LlmConfig 构建，密钥解密）。
- llm_client：OpenAI 兼容 LLM 客户端封装与解耦的响应数据结构（LLMResponse/ToolCall）。
- tools：AI 工具集 get_product_knowledge / search_customer_service_knowledge
  （经 common kb.search 检索，结果格式化为中文文本，需求 8.2-8.4）。
- ai_reply_engine：复用 CustomerAgent 的 Agent 循环（LLM → 工具调用 → 回传 →
  循环），基于检索内容生成中文回复；LLM 失败/超时/空内容回退默认回复并记
  「AI 回复失败」（process_result=ai_reply_failed，需求 8.1-8.5/8.7）。
- goods_card_fallback：商品卡片签名（anti-content）缺失 / 失效时降级为文本回复
  商品信息（复用任务 10.1 的签名检测），携带中文降级原因且不中断会话；不依赖
  签名的能力（关键词回复、默认回复、AI 文本回复、转人工）照常产出（需求 26.3-26.6）。
"""
