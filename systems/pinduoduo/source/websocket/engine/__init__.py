"""
文件用途：自动回复引擎包（engine）。

承载自动回复的纯逻辑组件（后续任务实现）：
- keyword_matcher：关键词规则按优先级匹配（全匹配/包含/正则，命中多条仅返回优先级最高一条）；
- business_hours：基于北京时间的营业时间判定（含跨午夜；未配置默认全天）；
- message_filter：过滤规则与黑名单判断（移出=逻辑失效）；
- risk_control：风控频率限制判定与风控日志数据生成；
- reply_engine：自动回复决策链编排（黑名单/过滤 → 非营业时间 → 风控 → 关键词
  → 商品专属 → AI → 默认回复 → 无匹配规则），并定义 ReplyDecision 纯数据结构。

已实现：
- message_filter（过滤规则命中与黑名单判断，纯逻辑）；
- keyword_matcher（关键词规则按优先级匹配，全匹配/包含/正则，停用不参与，
  命中多条仅返回优先级最高一条，返回回复类型与内容）；
- business_hours（基于北京时间的营业时间判定，含跨午夜；未配置默认全天）；
- risk_control（单会话/单店铺统计窗口内回复频率上限判定，达上限暂停并生成
  风控类型 frequency_limit 的风控日志数据，纯逻辑）；
- reply_engine（自动回复决策链编排，按固定优先级 黑名单/过滤 → 非营业时间 →
  风控 → 关键词 → 商品专属 → AI → 默认回复 → 无匹配规则 输出 ReplyDecision
  纯数据结构，复用上述各组件，纯逻辑）；
- message_consumer（消息处理全链路消费器，任务 19.2：把 PDDChannel 收消息 →
  pdd_message 解析 → FIFO 队列消费 → 转人工判定 → reply_engine 决策链 →
  知识库/AI 回复引擎 → SendMessage 发送回复/商品卡片签名降级 → 记消息/风控
  日志 → 系统事件通知（经 backend）串成一条端到端处理链）。
"""
