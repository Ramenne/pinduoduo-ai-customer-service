-- Structure only. No business rows, accounts, sessions or credentials.

SET NAMES utf8mb4;

CREATE TABLE `pdd_account` (
  `shop_pk` bigint NOT NULL COMMENT '关联店铺主键 shop.id（普通列，无外键）',
  `user_id` bigint DEFAULT NULL COMMENT '归属用户 ID（普通列，无外键）',
  `username` varchar(128) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '登录账号（拼多多账号）',
  `password_enc` text COLLATE utf8mb4_unicode_ci COMMENT '密码密文（加密存储，对外脱敏）',
  `cookies_enc` text COLLATE utf8mb4_unicode_ci COMMENT 'Cookie 密文（加密存储，对外脱敏）',
  `login_state` varchar(32) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '登录态：unverified/online/offline/relogin（枚举入字典）',
  `created_by` bigint DEFAULT NULL COMMENT '创建人用户 ID（普通列，关系由代码维护，不设外键）',
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键 ID（自增）',
  `created_at` datetime NOT NULL COMMENT '创建时间（北京时间）',
  `updated_at` datetime NOT NULL COMMENT '更新时间（北京时间，更新时自动刷新）',
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='账号凭据表（登录账号 / 密码密文 / Cookie 密文 / 登录态）';

CREATE TABLE `pdd_announcement` (
  `title` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '公告标题',
  `content` text COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '公告正文',
  `status` int NOT NULL COMMENT '状态：1=启用/0=停用（逻辑下线）',
  `publish_at` datetime DEFAULT NULL COMMENT '发布时间（北京时间）',
  `deleted_flag` tinyint(1) NOT NULL COMMENT '逻辑删除标记（True=已删除，仍保留）',
  `created_by` bigint DEFAULT NULL COMMENT '创建人用户 ID（普通列，关系由代码维护，不设外键）',
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键 ID（自增）',
  `created_at` datetime NOT NULL COMMENT '创建时间（北京时间）',
  `updated_at` datetime NOT NULL COMMENT '更新时间（北京时间，更新时自动刷新）',
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='公告表（标题 / 内容 / 启停用 / 逻辑删除）';

CREATE TABLE `pdd_blacklist` (
  `shop_pk` bigint NOT NULL COMMENT '关联店铺主键 shop.id（普通列，无外键）',
  `customer_uid` varchar(128) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '客户唯一标识 customer_uid',
  `is_active` tinyint(1) NOT NULL COMMENT '是否有效（移出=False，逻辑失效）',
  `created_by` bigint DEFAULT NULL COMMENT '创建人用户 ID（普通列，关系由代码维护，不设外键）',
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键 ID（自增）',
  `created_at` datetime NOT NULL COMMENT '创建时间（北京时间）',
  `updated_at` datetime NOT NULL COMMENT '更新时间（北京时间，更新时自动刷新）',
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='黑名单表（屏蔽客户 / 逻辑失效）';

CREATE TABLE `pdd_business_hours` (
  `shop_pk` bigint NOT NULL COMMENT '关联店铺主键 shop.id（普通列，无外键）',
  `start_time` time DEFAULT NULL COMMENT '营业开始时刻（北京时间）',
  `end_time` time DEFAULT NULL COMMENT '营业结束时刻（北京时间，可跨午夜）',
  `enabled` tinyint(1) NOT NULL COMMENT '是否启用',
  `created_by` bigint DEFAULT NULL COMMENT '创建人用户 ID（普通列，关系由代码维护，不设外键）',
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键 ID（自增）',
  `created_at` datetime NOT NULL COMMENT '创建时间（北京时间）',
  `updated_at` datetime NOT NULL COMMENT '更新时间（北京时间，更新时自动刷新）',
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='营业时间表（起止时刻 / 启停用，北京时间）';

CREATE TABLE `pdd_channel` (
  `channel_name` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '渠道名称（如 pinduoduo）',
  `description` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '渠道描述',
  `created_by` bigint DEFAULT NULL COMMENT '创建人用户 ID（普通列，关系由代码维护，不设外键）',
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键 ID（自增）',
  `created_at` datetime NOT NULL COMMENT '创建时间（北京时间）',
  `updated_at` datetime NOT NULL COMMENT '更新时间（北京时间，更新时自动刷新）',
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='渠道表（对接平台渠道，如 pinduoduo）';

CREATE TABLE `pdd_chat_message` (
  `shop_pk` bigint NOT NULL COMMENT '关联店铺主键 shop.id（普通列，无外键）',
  `customer_uid` varchar(128) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '客户唯一标识 customer_uid',
  `msg_id` varchar(64) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '拼多多消息 ID（落库去重用，可空）',
  `direction` varchar(16) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '消息方向：in=收/out=发（枚举入字典）',
  `msg_type` varchar(32) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '消息类型（文本/图片/视频/表情等，枚举入字典）',
  `content` text COLLATE utf8mb4_unicode_ci COMMENT '消息内容',
  `order_context` text COLLATE utf8mb4_unicode_ci COMMENT '订单上下文（JSON 文本）',
  `goods_context` text COLLATE utf8mb4_unicode_ci COMMENT '商品上下文（JSON 文本）',
  `msg_time` datetime DEFAULT NULL COMMENT '消息时间（北京时间）',
  `created_by` bigint DEFAULT NULL COMMENT '创建人用户 ID（普通列，关系由代码维护，不设外键）',
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键 ID（自增）',
  `created_at` datetime NOT NULL COMMENT '创建时间（北京时间）',
  `updated_at` datetime NOT NULL COMMENT '更新时间（北京时间，更新时自动刷新）',
  PRIMARY KEY (`id`),
  KEY `ix_pdd_chat_message_msg_id` (`msg_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='聊天消息表（会话消息 / 方向 / 类型 / 上下文）';

CREATE TABLE `pdd_chat_quick_phrase` (
  `owner_user_id` bigint NOT NULL,
  `shop_pk` bigint DEFAULT NULL,
  `title` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `content` text COLLATE utf8mb4_unicode_ci NOT NULL,
  `enabled` tinyint(1) NOT NULL DEFAULT '1',
  `created_by` bigint DEFAULT NULL COMMENT '创建人用户 ID（普通列，关系由代码维护，不设外键）',
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键 ID（自增）',
  `created_at` datetime NOT NULL COMMENT '创建时间（北京时间）',
  `updated_at` datetime NOT NULL COMMENT '更新时间（北京时间，更新时自动刷新）',
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `pdd_chat_sync_checkpoint` (
  `shop_pk` bigint NOT NULL,
  `customer_uid` varchar(128) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT '',
  `next_cursor` varchar(128) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `complete` tinyint(1) NOT NULL DEFAULT '0',
  `last_attempt_at` datetime DEFAULT NULL,
  `last_success_at` datetime DEFAULT NULL,
  `last_error` text COLLATE utf8mb4_unicode_ci,
  `created_by` bigint DEFAULT NULL COMMENT '创建人用户 ID（普通列，关系由代码维护，不设外键）',
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键 ID（自增）',
  `created_at` datetime NOT NULL COMMENT '创建时间（北京时间）',
  `updated_at` datetime NOT NULL COMMENT '更新时间（北京时间，更新时自动刷新）',
  `recovery_json` text COLLATE utf8mb4_unicode_ci COMMENT '连接缺口恢复游标与进度，不保存聊天正文',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uix_chat_sync_shop_customer` (`shop_pk`,`customer_uid`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `pdd_conversation` (
  `shop_pk` bigint NOT NULL COMMENT '关联店铺主键 shop.id（普通列，无外键）',
  `customer_uid` varchar(128) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '客户唯一标识 customer_uid',
  `nickname` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '客户昵称',
  `last_msg_at` datetime DEFAULT NULL COMMENT '最近消息时间（北京时间，用于倒序）',
  `unread_count` int NOT NULL COMMENT '未读消息数',
  `created_by` bigint DEFAULT NULL COMMENT '创建人用户 ID（普通列，关系由代码维护，不设外键）',
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键 ID（自增）',
  `created_at` datetime NOT NULL COMMENT '创建时间（北京时间）',
  `updated_at` datetime NOT NULL COMMENT '更新时间（北京时间，更新时自动刷新）',
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='会话表（店铺 / 客户会话基础信息）';

CREATE TABLE `pdd_conversation_control` (
  `shop_pk` bigint NOT NULL,
  `customer_uid` varchar(128) COLLATE utf8mb4_unicode_ci NOT NULL,
  `mode` varchar(16) COLLATE utf8mb4_unicode_ci NOT NULL,
  `revision` int NOT NULL,
  `reason` varchar(128) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `created_by` bigint DEFAULT NULL COMMENT '创建人用户 ID（普通列，关系由代码维护，不设外键）',
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键 ID（自增）',
  `created_at` datetime NOT NULL COMMENT '创建时间（北京时间）',
  `updated_at` datetime NOT NULL COMMENT '更新时间（北京时间，更新时自动刷新）',
  `supplement_text` text COLLATE utf8mb4_unicode_ci,
  `draft_json` mediumtext COLLATE utf8mb4_unicode_ci,
  `workspace_json` mediumtext COLLATE utf8mb4_unicode_ci,
  `harness_json` mediumtext COLLATE utf8mb4_unicode_ci,
  `attention_count` int DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uix_conversation_control` (`shop_pk`,`customer_uid`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `pdd_customer_service_knowledge` (
  `shop_pk` bigint NOT NULL COMMENT '关联店铺主键 shop.id（去重键一部分，无外键）',
  `title` varchar(512) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '知识标题（去重键一部分）',
  `content` text COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '知识内容（去重键一部分）',
  `tags` varchar(512) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '标签（逗号分隔）',
  `enabled` tinyint(1) NOT NULL COMMENT '是否启用（仅启用参与检索）',
  `created_by` bigint DEFAULT NULL COMMENT '创建人用户 ID（普通列，关系由代码维护，不设外键）',
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键 ID（自增）',
  `created_at` datetime NOT NULL COMMENT '创建时间（北京时间）',
  `updated_at` datetime NOT NULL COMMENT '更新时间（北京时间，更新时自动刷新）',
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='客服知识库表（问答标题 / 内容 / 启停用）';

CREATE TABLE `pdd_default_reply` (
  `shop_pk` bigint NOT NULL COMMENT '关联店铺主键 shop.id（普通列，无外键）',
  `content` text COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '默认回复内容',
  `enabled` tinyint(1) NOT NULL COMMENT '是否启用',
  `reply_once` tinyint(1) NOT NULL COMMENT '是否只回复一次（同一客户仅发送一次默认回复）',
  `created_by` bigint DEFAULT NULL COMMENT '创建人用户 ID（普通列，关系由代码维护，不设外键）',
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键 ID（自增）',
  `created_at` datetime NOT NULL COMMENT '创建时间（北京时间）',
  `updated_at` datetime NOT NULL COMMENT '更新时间（北京时间，更新时自动刷新）',
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='默认回复表（店铺兜底回复内容 / 启停用 / 只回复一次）';

CREATE TABLE `pdd_default_reply_record` (
  `shop_pk` bigint NOT NULL COMMENT '关联店铺主键 shop.id（普通列，无外键）',
  `customer_uid` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '已收到默认回复的客户唯一标识',
  `created_by` bigint DEFAULT NULL COMMENT '创建人用户 ID（普通列，关系由代码维护，不设外键）',
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键 ID（自增）',
  `created_at` datetime NOT NULL COMMENT '创建时间（北京时间）',
  `updated_at` datetime NOT NULL COMMENT '更新时间（北京时间，更新时自动刷新）',
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='默认回复发送记录表（支撑只回复一次：已回复客户）';

CREATE TABLE `pdd_feedback` (
  `user_id` int DEFAULT NULL COMMENT '提交用户 ID（普通列，无外键）',
  `content` text COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '反馈内容',
  `contact` varchar(128) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '联系方式',
  `status` varchar(32) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '处理状态：pending/processing/done/closed（枚举入字典）',
  `reply` text COLLATE utf8mb4_unicode_ci COMMENT '管理员回复',
  `created_by` bigint DEFAULT NULL COMMENT '创建人用户 ID（普通列，关系由代码维护，不设外键）',
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键 ID（自增）',
  `created_at` datetime NOT NULL COMMENT '创建时间（北京时间）',
  `updated_at` datetime NOT NULL COMMENT '更新时间（北京时间，更新时自动刷新）',
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='意见反馈表（提交内容 / 处理状态 / 管理员回复）';

CREATE TABLE `pdd_goods_reply` (
  `shop_pk` bigint NOT NULL COMMENT '关联店铺主键 shop.id（普通列，无外键）',
  `goods_id` varchar(128) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '拼多多商品业务标识 goods_id（无外键）',
  `reply_type` varchar(32) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '回复类型：text/image（枚举入字典）',
  `reply_content` text COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '回复内容',
  `enabled` tinyint(1) NOT NULL COMMENT '是否启用',
  `created_by` bigint DEFAULT NULL COMMENT '创建人用户 ID（普通列，关系由代码维护，不设外键）',
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键 ID（自增）',
  `created_at` datetime NOT NULL COMMENT '创建时间（北京时间）',
  `updated_at` datetime NOT NULL COMMENT '更新时间（北京时间，更新时自动刷新）',
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='商品专属回复表（绑定 goods_id，优先级高于默认）';

CREATE TABLE `pdd_keyword_rule` (
  `shop_pk` bigint NOT NULL COMMENT '关联店铺主键 shop.id（普通列，无外键）',
  `keyword` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '关键词（匹配文本或正则）',
  `match_type` varchar(32) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '匹配方式：full/contains/regex（枚举入字典）',
  `reply_type` varchar(32) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '回复类型：text/image（枚举入字典）',
  `reply_content` text COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '回复内容（文本或图片地址）',
  `priority` int NOT NULL COMMENT '优先级（越大越优先）',
  `enabled` tinyint(1) NOT NULL COMMENT '是否启用（停用不参与匹配）',
  `created_by` bigint DEFAULT NULL COMMENT '创建人用户 ID（普通列，关系由代码维护，不设外键）',
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键 ID（自增）',
  `created_at` datetime NOT NULL COMMENT '创建时间（北京时间）',
  `updated_at` datetime NOT NULL COMMENT '更新时间（北京时间，更新时自动刷新）',
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='关键词规则表（匹配方式 / 回复类型 / 优先级 / 启停用）';

CREATE TABLE `pdd_knowledge_curation` (
  `shop_pk` bigint NOT NULL,
  `owner_user_id` bigint NOT NULL,
  `customer_uid` varchar(128) COLLATE utf8mb4_unicode_ci NOT NULL,
  `batch_key` varchar(36) COLLATE utf8mb4_unicode_ci NOT NULL,
  `state` varchar(24) COLLATE utf8mb4_unicode_ci NOT NULL,
  `snapshot_json` mediumtext COLLATE utf8mb4_unicode_ci NOT NULL,
  `result_json` mediumtext COLLATE utf8mb4_unicode_ci,
  `token` varchar(36) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `attempts` int NOT NULL,
  `due_at` double NOT NULL,
  `error` varchar(128) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `created_by` bigint DEFAULT NULL COMMENT '创建人用户 ID（普通列，关系由代码维护，不设外键）',
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键 ID（自增）',
  `created_at` datetime NOT NULL COMMENT '创建时间（北京时间）',
  `updated_at` datetime NOT NULL COMMENT '更新时间（北京时间，更新时自动刷新）',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uix_curation_batch` (`batch_key`),
  KEY `ix_pdd_knowledge_curation_due_at` (`due_at`),
  KEY `ix_pdd_knowledge_curation_shop_pk` (`shop_pk`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `pdd_knowledge_version` (
  `shop_pk` bigint NOT NULL,
  `kind` varchar(16) COLLATE utf8mb4_unicode_ci NOT NULL,
  `item_id` bigint NOT NULL,
  `version_no` int NOT NULL,
  `content_hash` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `payload_json` mediumtext COLLATE utf8mb4_unicode_ci NOT NULL,
  `operation` varchar(32) COLLATE utf8mb4_unicode_ci NOT NULL,
  `source` varchar(128) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `created_by` bigint DEFAULT NULL COMMENT '创建人用户 ID（普通列，关系由代码维护，不设外键）',
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键 ID（自增）',
  `created_at` datetime NOT NULL COMMENT '创建时间（北京时间）',
  `updated_at` datetime NOT NULL COMMENT '更新时间（北京时间，更新时自动刷新）',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uix_knowledge_version` (`kind`,`item_id`,`version_no`),
  KEY `ix_pdd_knowledge_version_shop_pk` (`shop_pk`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `pdd_llm_config` (
  `shop_pk` bigint DEFAULT NULL COMMENT '关联店铺主键 shop.id；为空表示全局（无外键）',
  `provider_type` varchar(32) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'openai_compatible' COMMENT '接口类型：openai_compatible/anthropic/gemini/dashscope_app',
  `model_name` varchar(128) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '模型名称',
  `api_key_enc` text COLLATE utf8mb4_unicode_ci COMMENT 'API 密钥密文（加密存储，对外脱敏）',
  `api_base` varchar(512) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT 'API 接口地址',
  `instructions` text COLLATE utf8mb4_unicode_ci COMMENT '模型指令（JSON 文本）',
  `ai_enabled` tinyint(1) NOT NULL COMMENT '是否启用 AI 回复',
  `created_by` bigint DEFAULT NULL COMMENT '创建人用户 ID（普通列，关系由代码维护，不设外键）',
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键 ID（自增）',
  `created_at` datetime NOT NULL COMMENT '创建时间（北京时间）',
  `updated_at` datetime NOT NULL COMMENT '更新时间（北京时间，更新时自动刷新）',
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='LLM 配置表（接口类型 / 模型 / 密钥密文 / 指令 / AI 开关）';

CREATE TABLE `pdd_message_delivery` (
  `shop_pk` bigint NOT NULL,
  `customer_uid` varchar(128) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
  `operation_key` varchar(128) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
  `inbox_id` bigint DEFAULT NULL,
  `kind` varchar(20) COLLATE utf8mb4_unicode_ci NOT NULL,
  `body_hash` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `body` text COLLATE utf8mb4_unicode_ci NOT NULL,
  `baseline_ids` text COLLATE utf8mb4_unicode_ci,
  `baseline_at` double DEFAULT NULL,
  `state` varchar(20) COLLATE utf8mb4_unicode_ci NOT NULL,
  `platform_id` varchar(128) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `request_id` varchar(36) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
  `due_at` double NOT NULL,
  `checks` int NOT NULL,
  `reason` varchar(128) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `resolved_by` bigint DEFAULT NULL,
  `resolution_note` varchar(500) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `created_by` bigint DEFAULT NULL COMMENT '创建人用户 ID（普通列，关系由代码维护，不设外键）',
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键 ID（自增）',
  `created_at` datetime NOT NULL COMMENT '创建时间（北京时间）',
  `updated_at` datetime NOT NULL COMMENT '更新时间（北京时间，更新时自动刷新）',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uix_message_delivery` (`shop_pk`,`operation_key`),
  UNIQUE KEY `uix_message_request` (`shop_pk`,`request_id`),
  KEY `ix_pdd_message_delivery_shop_pk` (`shop_pk`),
  KEY `ix_pdd_message_delivery_inbox_id` (`inbox_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `pdd_message_filter_rule` (
  `shop_pk` bigint NOT NULL COMMENT '关联店铺主键 shop.id（普通列，无外键）',
  `condition_type` varchar(32) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '过滤条件类型（枚举入字典）',
  `condition_value` varchar(512) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '过滤条件值',
  `enabled` tinyint(1) NOT NULL COMMENT '是否启用',
  `created_by` bigint DEFAULT NULL COMMENT '创建人用户 ID（普通列，关系由代码维护，不设外键）',
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键 ID（自增）',
  `created_at` datetime NOT NULL COMMENT '创建时间（北京时间）',
  `updated_at` datetime NOT NULL COMMENT '更新时间（北京时间，更新时自动刷新）',
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='消息过滤规则表（过滤条件 / 启停用）';

CREATE TABLE `pdd_message_inbox` (
  `shop_pk` bigint NOT NULL,
  `customer_uid` varchar(128) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
  `message_id` varchar(128) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
  `payload` text COLLATE utf8mb4_unicode_ci NOT NULL,
  `state` varchar(20) COLLATE utf8mb4_unicode_ci NOT NULL,
  `token` varchar(36) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `due_at` double NOT NULL,
  `attempts` int NOT NULL,
  `result` varchar(80) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `recorded` int NOT NULL,
  `created_by` bigint DEFAULT NULL COMMENT '创建人用户 ID（普通列，关系由代码维护，不设外键）',
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键 ID（自增）',
  `created_at` datetime NOT NULL COMMENT '创建时间（北京时间）',
  `updated_at` datetime NOT NULL COMMENT '更新时间（北京时间，更新时自动刷新）',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uix_message_inbox` (`shop_pk`,`customer_uid`,`message_id`),
  KEY `ix_pdd_message_inbox_shop_pk` (`shop_pk`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `pdd_message_log` (
  `shop_pk` bigint NOT NULL COMMENT '关联店铺主键 shop.id（普通列，无外键）',
  `customer_uid` varchar(128) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '客户唯一标识 customer_uid',
  `message_content` text COLLATE utf8mb4_unicode_ci COMMENT '原始消息内容',
  `process_result` varchar(64) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '处理结果（枚举入字典）',
  `reply_content` text COLLATE utf8mb4_unicode_ci COMMENT '回复内容',
  `log_time` datetime DEFAULT NULL COMMENT '日志时间（北京时间）',
  `created_by` bigint DEFAULT NULL COMMENT '创建人用户 ID（普通列，关系由代码维护，不设外键）',
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键 ID（自增）',
  `created_at` datetime NOT NULL COMMENT '创建时间（北京时间）',
  `updated_at` datetime NOT NULL COMMENT '更新时间（北京时间，更新时自动刷新）',
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='消息日志表（消息处理记录 / 处理结果）';

CREATE TABLE `pdd_message_poll_state` (
  `shop_pk` bigint NOT NULL,
  `since` double NOT NULL,
  `checked_at` double NOT NULL,
  `created_by` bigint DEFAULT NULL COMMENT '创建人用户 ID（普通列，关系由代码维护，不设外键）',
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键 ID（自增）',
  `created_at` datetime NOT NULL COMMENT '创建时间（北京时间）',
  `updated_at` datetime NOT NULL COMMENT '更新时间（北京时间，更新时自动刷新）',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uix_message_poll_state` (`shop_pk`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `pdd_notify_channel` (
  `shop_pk` bigint DEFAULT NULL COMMENT '归属店铺主键 shop.id（普通列，无外键）',
  `channel_type` varchar(32) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '渠道类型：email/webhook/wecom（枚举入字典）',
  `target` varchar(512) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '通知目标（邮箱/URL 等）',
  `enabled` tinyint(1) NOT NULL COMMENT '是否启用',
  `created_by` bigint DEFAULT NULL COMMENT '创建人用户 ID（普通列，关系由代码维护，不设外键）',
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键 ID（自增）',
  `created_at` datetime NOT NULL COMMENT '创建时间（北京时间）',
  `updated_at` datetime NOT NULL COMMENT '更新时间（北京时间，更新时自动刷新）',
  PRIMARY KEY (`id`),
  KEY `ix_pdd_notify_channel_shop_pk` (`shop_pk`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='通知渠道表（渠道类型 / 目标地址 / 启停用）';

CREATE TABLE `pdd_notify_record` (
  `shop_pk` bigint DEFAULT NULL COMMENT '归属店铺主键 shop.id（普通列，无外键）',
  `channel_id` bigint DEFAULT NULL COMMENT '通知渠道 ID（普通列，无外键）',
  `event_type` varchar(64) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '事件类型（连接断开/登录态失效/风控触发等）',
  `content` text COLLATE utf8mb4_unicode_ci COMMENT '通知内容',
  `send_result` varchar(32) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '发送结果（成功/失败）',
  `log_time` datetime DEFAULT NULL COMMENT '日志时间（北京时间）',
  `created_by` bigint DEFAULT NULL COMMENT '创建人用户 ID（普通列，关系由代码维护，不设外键）',
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键 ID（自增）',
  `created_at` datetime NOT NULL COMMENT '创建时间（北京时间）',
  `updated_at` datetime NOT NULL COMMENT '更新时间（北京时间，更新时自动刷新）',
  PRIMARY KEY (`id`),
  KEY `ix_pdd_notify_record_shop_pk` (`shop_pk`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='通知记录表（渠道 / 事件类型 / 发送结果）';

CREATE TABLE `pdd_product` (
  `shop_pk` bigint NOT NULL COMMENT '关联店铺主键 shop.id（业务键一部分，无外键）',
  `goods_id` varchar(128) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '拼多多商品业务标识（业务键一部分，无外键）',
  `goods_name` varchar(512) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '商品名称',
  `price` decimal(12,2) DEFAULT NULL COMMENT '价格',
  `sold_quantity` int DEFAULT NULL COMMENT '已售数量',
  `thumb_url` varchar(512) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '商品缩略图 URL',
  `specifications` text COLLATE utf8mb4_unicode_ci COMMENT '商品规格（JSON 文本，来自拼多多商品详情）',
  `status` int NOT NULL COMMENT '状态：1=启用，0=停用（逻辑删除）',
  `created_by` bigint DEFAULT NULL COMMENT '创建人用户 ID（普通列，关系由代码维护，不设外键）',
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键 ID（自增）',
  `created_at` datetime NOT NULL COMMENT '创建时间（北京时间）',
  `updated_at` datetime NOT NULL COMMENT '更新时间（北京时间，更新时自动刷新）',
  `synced_at` datetime DEFAULT NULL,
  `specs_updated_at` datetime DEFAULT NULL,
  `specs_checked_at` datetime DEFAULT NULL,
  `specs_error` varchar(256) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `platform_on_sale` int DEFAULT NULL COMMENT '完整平台快照：1在售，0未在售，NULL未核对；独立于手动启停',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uix_pdd_product_shop_goods` (`shop_pk`,`goods_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='商品表（拼多多店铺商品信息）';

CREATE TABLE `pdd_product_knowledge` (
  `shop_pk` bigint NOT NULL COMMENT '关联店铺主键 shop.id（业务键一部分，无外键）',
  `goods_id` varchar(128) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '拼多多商品业务标识（业务键一部分，无外键）',
  `goods_name` varchar(512) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '商品名称',
  `price` decimal(12,2) DEFAULT NULL COMMENT '价格',
  `price_min` decimal(12,2) DEFAULT NULL COMMENT '最低价',
  `price_max` decimal(12,2) DEFAULT NULL COMMENT '最高价',
  `sold_quantity` int DEFAULT NULL COMMENT '已售数量',
  `thumb_url` varchar(512) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '商品缩略图 URL',
  `specifications` text COLLATE utf8mb4_unicode_ci COMMENT '商品规格（JSON 文本）',
  `extracted_content` text COLLATE utf8mb4_unicode_ci COMMENT '抽取内容（供 AI 检索）',
  `last_extracted_at` datetime DEFAULT NULL COMMENT '最近抽取时间（北京时间）',
  `status` int NOT NULL COMMENT '状态：1=启用，0=停用（逻辑删除）',
  `created_by` bigint DEFAULT NULL COMMENT '创建人用户 ID（普通列，关系由代码维护，不设外键）',
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键 ID（自增）',
  `created_at` datetime NOT NULL COMMENT '创建时间（北京时间）',
  `updated_at` datetime NOT NULL COMMENT '更新时间（北京时间，更新时自动刷新）',
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='商品知识库表（按 goods_id 维护商品问答知识）';

CREATE TABLE `pdd_product_sync_job` (
  `shop_pk` bigint NOT NULL,
  `product_id` bigint DEFAULT NULL,
  `kind` varchar(24) COLLATE utf8mb4_unicode_ci NOT NULL,
  `state` varchar(24) COLLATE utf8mb4_unicode_ci NOT NULL,
  `run_token` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `owner_user_id` bigint DEFAULT NULL,
  `revision` int NOT NULL,
  `total` int NOT NULL,
  `completed` int NOT NULL,
  `failed` int NOT NULL,
  `message` text COLLATE utf8mb4_unicode_ci,
  `finished_at` datetime DEFAULT NULL,
  `created_by` bigint DEFAULT NULL COMMENT '创建人用户 ID（普通列，关系由代码维护，不设外键）',
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键 ID（自增）',
  `created_at` datetime NOT NULL COMMENT '创建时间（北京时间）',
  `updated_at` datetime NOT NULL COMMENT '更新时间（北京时间，更新时自动刷新）',
  PRIMARY KEY (`id`),
  UNIQUE KEY `shop_pk` (`shop_pk`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `pdd_risk_log` (
  `shop_pk` bigint NOT NULL COMMENT '关联店铺主键 shop.id（普通列，无外键）',
  `risk_type` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '风控类型（枚举入字典）',
  `trigger_reason` text COLLATE utf8mb4_unicode_ci COMMENT '触发原因',
  `log_time` datetime DEFAULT NULL COMMENT '日志时间（北京时间）',
  `created_by` bigint DEFAULT NULL COMMENT '创建人用户 ID（普通列，关系由代码维护，不设外键）',
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键 ID（自增）',
  `created_at` datetime NOT NULL COMMENT '创建时间（北京时间）',
  `updated_at` datetime NOT NULL COMMENT '更新时间（北京时间，更新时自动刷新）',
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='风控日志表（风控触发记录 / 类型 / 原因）';

CREATE TABLE `pdd_risk_rule` (
  `shop_pk` bigint NOT NULL COMMENT '关联店铺主键 shop.id（普通列，无外键）',
  `session_reply_limit` int DEFAULT NULL COMMENT '单会话窗口内回复次数上限',
  `shop_reply_limit` int DEFAULT NULL COMMENT '单店铺窗口内回复次数上限',
  `window_seconds` int DEFAULT NULL COMMENT '统计窗口（秒）',
  `enabled` tinyint(1) NOT NULL COMMENT '是否启用',
  `created_by` bigint DEFAULT NULL COMMENT '创建人用户 ID（普通列，关系由代码维护，不设外键）',
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键 ID（自增）',
  `created_at` datetime NOT NULL COMMENT '创建时间（北京时间）',
  `updated_at` datetime NOT NULL COMMENT '更新时间（北京时间，更新时自动刷新）',
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='风控规则表（单会话 / 单店铺频率上限与统计窗口）';

CREATE TABLE `pdd_scheduled_task` (
  `task_key` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '任务键（cookie_refresh/product_sync 等）',
  `task_name` varchar(128) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '任务名称（中文）',
  `schedule_type` varchar(16) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '调度方式：cron/interval（枚举入字典）',
  `schedule_config` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '调度配置（cron 表达式或间隔）',
  `enabled` tinyint(1) NOT NULL COMMENT '是否启用',
  `last_run_at` datetime DEFAULT NULL COMMENT '上次执行时间（北京时间）',
  `next_run_at` datetime DEFAULT NULL COMMENT '下次执行时间（北京时间）',
  `created_by` bigint DEFAULT NULL COMMENT '创建人用户 ID（普通列，关系由代码维护，不设外键）',
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键 ID（自增）',
  `created_at` datetime NOT NULL COMMENT '创建时间（北京时间）',
  `updated_at` datetime NOT NULL COMMENT '更新时间（北京时间，更新时自动刷新）',
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='定时任务表（任务键 / 调度方式 / 调度配置 / 启停用）';

CREATE TABLE `pdd_shop` (
  `channel_id` bigint DEFAULT NULL COMMENT '所属渠道 ID（普通列，无外键）',
  `shop_id` varchar(128) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '拼多多店铺业务标识（业务键，非主键）',
  `shop_name` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '店铺名称',
  `shop_logo` varchar(512) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '店铺 Logo URL',
  `owner_user_id` bigint DEFAULT NULL COMMENT '归属用户 ID（业务键一部分，无外键）',
  `remark` varchar(512) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '备注',
  `status` int NOT NULL COMMENT '状态：1=启用，0=停用（逻辑删除）',
  `created_by` bigint DEFAULT NULL COMMENT '创建人用户 ID（普通列，关系由代码维护，不设外键）',
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键 ID（自增）',
  `created_at` datetime NOT NULL COMMENT '创建时间（北京时间）',
  `updated_at` datetime NOT NULL COMMENT '更新时间（北京时间，更新时自动刷新）',
  `merged_into_shop_pk` bigint DEFAULT NULL,
  `canonical_shop_id` varchar(128) COLLATE utf8mb4_unicode_ci GENERATED ALWAYS AS ((case when (`merged_into_shop_pk` is null) then `shop_id` else NULL end)) VIRTUAL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uix_pdd_shop_owner_shopid` (`owner_user_id`,`shop_id`),
  UNIQUE KEY `uix_pdd_shop_canonical_identity` (`canonical_shop_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='店铺表（拼多多店铺信息 / 归属用户 / 启停用）';

CREATE TABLE `pdd_sys_dict` (
  `dict_type` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '字典类型（枚举分组键）',
  `dict_key` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '字典键（枚举取值键）',
  `dict_label` varchar(128) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '字典标签（中文展示文案）',
  `order_no` int NOT NULL COMMENT '排序序号（升序）',
  `enabled` tinyint(1) NOT NULL COMMENT '是否启用',
  `created_by` bigint DEFAULT NULL COMMENT '创建人用户 ID（普通列，关系由代码维护，不设外键）',
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键 ID（自增）',
  `created_at` datetime NOT NULL COMMENT '创建时间（北京时间）',
  `updated_at` datetime NOT NULL COMMENT '更新时间（北京时间，更新时自动刷新）',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uix_pdd_sys_dict_type_key` (`dict_type`,`dict_key`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='数据字典表（枚举类型 / 键 / 中文标签 / 排序）';

CREATE TABLE `pdd_sys_menu` (
  `menu_key` varchar(128) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '菜单键（唯一标识，用于路由/权限）',
  `title` varchar(128) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '菜单标题（中文）',
  `parent_key` varchar(128) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '父级菜单键（顶级为空，无外键）',
  `admin_only` tinyint(1) NOT NULL COMMENT '是否仅管理员可见',
  `order_no` int NOT NULL COMMENT '菜单排序序号（升序）',
  `created_by` bigint DEFAULT NULL COMMENT '创建人用户 ID（普通列，关系由代码维护，不设外键）',
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键 ID（自增）',
  `created_at` datetime NOT NULL COMMENT '创建时间（北京时间）',
  `updated_at` datetime NOT NULL COMMENT '更新时间（北京时间，更新时自动刷新）',
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='菜单表（菜单键 / 标题 / 层级 / 仅管理员可见 / 排序）';

CREATE TABLE `pdd_sys_permission` (
  `resource_key` varchar(128) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '资源键（受保护资源标识）',
  `action` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '操作（view/create/update/disable 等）',
  `description` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '权限描述',
  `created_by` bigint DEFAULT NULL COMMENT '创建人用户 ID（普通列，关系由代码维护，不设外键）',
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键 ID（自增）',
  `created_at` datetime NOT NULL COMMENT '创建时间（北京时间）',
  `updated_at` datetime NOT NULL COMMENT '更新时间（北京时间，更新时自动刷新）',
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='权限表（受保护资源键 + 操作）';

CREATE TABLE `pdd_sys_role` (
  `role_name` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '角色名称',
  `is_admin` tinyint(1) NOT NULL COMMENT '是否管理员角色',
  `is_default` tinyint(1) NOT NULL DEFAULT '0' COMMENT '是否注册默认角色',
  `status` int NOT NULL COMMENT '状态：1=启用，0=停用',
  `created_by` bigint DEFAULT NULL COMMENT '创建人用户 ID（普通列，关系由代码维护，不设外键）',
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键 ID（自增）',
  `created_at` datetime NOT NULL COMMENT '创建时间（北京时间）',
  `updated_at` datetime NOT NULL COMMENT '更新时间（北京时间，更新时自动刷新）',
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='角色表（角色名 / 是否管理员 / 启停用）';

CREATE TABLE `pdd_sys_role_permission` (
  `role_id` bigint NOT NULL COMMENT '角色 ID（普通列，无外键）',
  `permission_id` bigint NOT NULL COMMENT '权限 ID（普通列，无外键）',
  `enabled` tinyint(1) NOT NULL DEFAULT '1' COMMENT '映射是否有效（软删除标记）',
  `created_by` bigint DEFAULT NULL COMMENT '创建人用户 ID（普通列，关系由代码维护，不设外键）',
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键 ID（自增）',
  `created_at` datetime NOT NULL COMMENT '创建时间（北京时间）',
  `updated_at` datetime NOT NULL COMMENT '更新时间（北京时间，更新时自动刷新）',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uix_pdd_sys_role_perm_role_perm` (`role_id`,`permission_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='角色-权限映射表（多对多桥接）';

CREATE TABLE `pdd_sys_setting` (
  `setting_key` varchar(128) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '设置键（如 theme.color / proxy.enabled）',
  `setting_value` text COLLATE utf8mb4_unicode_ci COMMENT '设置值（JSON 文本；敏感值加密存储）',
  `scope` varchar(16) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '作用域：global/user',
  `owner_user_id` bigint DEFAULT NULL COMMENT '用户级设置归属用户 ID（普通列，无外键）',
  `created_by` bigint DEFAULT NULL COMMENT '创建人用户 ID（普通列，关系由代码维护，不设外键）',
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键 ID（自增）',
  `created_at` datetime NOT NULL COMMENT '创建时间（北京时间）',
  `updated_at` datetime NOT NULL COMMENT '更新时间（北京时间，更新时自动刷新）',
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='系统设置表（键值对存储，全局 / 用户维度）';

CREATE TABLE `pdd_sys_user` (
  `username` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '登录用户名（全局唯一）',
  `password_hash` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '密码哈希（仅存哈希，绝不存明文）',
  `role_id` bigint DEFAULT NULL COMMENT '所属角色 ID（普通列，无外键）',
  `status` int NOT NULL COMMENT '状态：1=启用，0=停用（逻辑删除）',
  `email` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '注册邮箱（邮箱验证码注册用，全局唯一由代码校验）',
  `wechat` varchar(64) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '微信号（个人联系方式）',
  `qq` varchar(32) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT 'QQ 号（个人联系方式）',
  `created_by` bigint DEFAULT NULL COMMENT '创建人用户 ID（普通列，关系由代码维护，不设外键）',
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键 ID（自增）',
  `created_at` datetime NOT NULL COMMENT '创建时间（北京时间）',
  `updated_at` datetime NOT NULL COMMENT '更新时间（北京时间，更新时自动刷新）',
  PRIMARY KEY (`id`),
  UNIQUE KEY `username` (`username`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='系统用户表（登录账号 / 密码哈希 / 角色 / 联系方式）';

CREATE TABLE `pdd_system_log` (
  `level` varchar(16) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '日志级别：info/warning/error',
  `module` varchar(128) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '来源模块',
  `content` text COLLATE utf8mb4_unicode_ci COMMENT '日志内容',
  `log_time` datetime DEFAULT NULL COMMENT '日志时间（北京时间）',
  `created_by` bigint DEFAULT NULL COMMENT '创建人用户 ID（普通列，关系由代码维护，不设外键）',
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键 ID（自增）',
  `created_at` datetime NOT NULL COMMENT '创建时间（北京时间）',
  `updated_at` datetime NOT NULL COMMENT '更新时间（北京时间，更新时自动刷新）',
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='系统日志表（级别 / 模块 / 内容，禁用 debug）';

CREATE TABLE `pdd_task_run_log` (
  `task_key` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '任务键',
  `run_result` varchar(16) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '执行结果：success/failed（枚举入字典）',
  `message` text COLLATE utf8mb4_unicode_ci COMMENT '执行信息',
  `log_time` datetime DEFAULT NULL COMMENT '执行时间（北京时间）',
  `created_by` bigint DEFAULT NULL COMMENT '创建人用户 ID（普通列，关系由代码维护，不设外键）',
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键 ID（自增）',
  `created_at` datetime NOT NULL COMMENT '创建时间（北京时间）',
  `updated_at` datetime NOT NULL COMMENT '更新时间（北京时间，更新时自动刷新）',
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='定时任务执行日志表（任务键 / 执行结果 / 耗时）';

CREATE TABLE `pdd_transfer_keyword` (
  `shop_pk` bigint NOT NULL COMMENT '关联店铺主键 shop.id（普通列，无外键）',
  `keyword` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '转人工关键词',
  `enabled` tinyint(1) NOT NULL COMMENT '是否启用',
  `created_by` bigint DEFAULT NULL COMMENT '创建人用户 ID（普通列，关系由代码维护，不设外键）',
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键 ID（自增）',
  `created_at` datetime NOT NULL COMMENT '创建时间（北京时间）',
  `updated_at` datetime NOT NULL COMMENT '更新时间（北京时间，更新时自动刷新）',
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='转人工关键词表（触发转人工的关键词配置）';

-- 0.1.21/0.1.22模型生成补充；上方为原0.1.18生产结构导出。
CREATE TABLE pdd_context_archive (
	shop_pk BIGINT NOT NULL, 
	owner_user_id BIGINT NOT NULL, 
	customer_uid VARCHAR(128) COLLATE utf8mb4_bin NOT NULL, 
	kind VARCHAR(24) NOT NULL, 
	stable_id VARCHAR(96) NOT NULL, 
	source_hash VARCHAR(64) NOT NULL, 
	payload_json MEDIUMTEXT NOT NULL, 
	retrieval_text TEXT NOT NULL, 
	summary_json TEXT, 
	state VARCHAR(24) NOT NULL, 
	token VARCHAR(36), 
	attempts INTEGER NOT NULL, 
	due_at DOUBLE NOT NULL, 
	error VARCHAR(128), 
	inbox_id BIGINT, 
	delivery_key VARCHAR(128), 
	created_by BIGINT COMMENT '创建人用户 ID（普通列，关系由代码维护，不设外键）', 
	id BIGINT NOT NULL COMMENT '主键 ID（自增）' AUTO_INCREMENT, 
	created_at DATETIME NOT NULL COMMENT '创建时间（北京时间）', 
	updated_at DATETIME NOT NULL COMMENT '更新时间（北京时间，更新时自动刷新）', 
	PRIMARY KEY (id), 
	CONSTRAINT uix_context_snapshot UNIQUE (shop_pk, customer_uid, kind, stable_id, source_hash)
);

CREATE INDEX ix_context_scope ON pdd_context_archive (shop_pk, customer_uid, kind, id);

CREATE INDEX ix_pdd_context_archive_due_at ON pdd_context_archive (due_at);

CREATE INDEX ix_pdd_context_archive_inbox_id ON pdd_context_archive (inbox_id);

CREATE TABLE pdd_harness_experience (
	shop_pk BIGINT NOT NULL, 
	owner_user_id BIGINT NOT NULL, 
	customer_uid VARCHAR(128) COLLATE utf8mb4_bin NOT NULL, 
	source_key VARCHAR(128) COLLATE utf8mb4_bin NOT NULL, 
	category VARCHAR(32) NOT NULL, 
	title VARCHAR(200) NOT NULL, 
	observation TEXT NOT NULL, 
	guidance TEXT NOT NULL, 
	trigger_terms VARCHAR(400) NOT NULL, 
	source_json TEXT NOT NULL, 
	state VARCHAR(24) NOT NULL, 
	version_no INTEGER NOT NULL, 
	validation_note VARCHAR(1000) NOT NULL, 
	created_by BIGINT COMMENT '创建人用户 ID（普通列，关系由代码维护，不设外键）', 
	id BIGINT NOT NULL COMMENT '主键 ID（自增）' AUTO_INCREMENT, 
	created_at DATETIME NOT NULL COMMENT '创建时间（北京时间）', 
	updated_at DATETIME NOT NULL COMMENT '更新时间（北京时间，更新时自动刷新）', 
	PRIMARY KEY (id), 
	CONSTRAINT uix_harness_observation UNIQUE (shop_pk, source_key)
);

CREATE INDEX ix_pdd_harness_experience_shop_pk ON pdd_harness_experience (shop_pk);
