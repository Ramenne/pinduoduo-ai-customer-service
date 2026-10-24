-- 仅用于已有0.1.17库；空库直接导入schema.sql，不重复执行本补丁。
ALTER TABLE pdd_conversation_control ADD COLUMN harness_json MEDIUMTEXT NULL;
