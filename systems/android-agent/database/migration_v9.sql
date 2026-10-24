ALTER TABLE seller_jobs ADD COLUMN control_state TEXT NOT NULL DEFAULT 'active' CHECK(control_state IN ('active','paused','pausing','stopping'));
ALTER TABLE seller_jobs ADD COLUMN outcomes_json TEXT NOT NULL DEFAULT '{}';
INSERT INTO oin_schema_migrations(version,applied_at) VALUES (9,strftime('%s','now'));
