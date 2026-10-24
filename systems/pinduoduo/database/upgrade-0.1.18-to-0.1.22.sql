-- 模型生成的两表加法结构，仅用于尚未有这两表的本地旧副本；不执行生产迁移。
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
