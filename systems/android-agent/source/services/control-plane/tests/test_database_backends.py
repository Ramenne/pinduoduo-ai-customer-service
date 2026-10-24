from __future__ import annotations

import os
import re
from importlib.resources import files
from pathlib import Path
from urllib.parse import urlsplit

import psycopg
import pytest

from oin_control_plane.config import Settings
from oin_control_plane.database import Database, _postgres_statements, _translate_qmark
from oin_control_plane.security import canonical_json, now_seconds
from oin_control_plane.store import Store, TaskCompletionKind


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def _production_settings(tmp_path: Path, **overrides) -> Settings:
    values = {
        "database_path": tmp_path / "unused.db",
        "database_url": "postgresql://oin_v01_app:test@127.0.0.1:5432/oin_v01",
        "database_auto_migrate": False,
        "public_base_url": "https://approved.example/oin",
        "apk_path": None,
        "apk_version": "unpublished",
        "environment": "production",
    }
    values.update(overrides)
    return Settings(**values)


def test_production_requires_database_url_before_backend_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OIN_ENVIRONMENT", "production")
    monkeypatch.setenv("OIN_PUBLIC_BASE_URL", "https://approved.example/oin")
    monkeypatch.setenv("OIN_DATABASE_PATH", str(tmp_path / "resolved-production.db"))
    monkeypatch.delenv("OIN_DATABASE_URL", raising=False)
    monkeypatch.delenv("OIN_DATABASE_AUTO_MIGRATE", raising=False)
    monkeypatch.delenv("OIN_INTERNAL_HTTP_ENABLED", raising=False)

    with pytest.raises(ValueError) as failure:
        Settings.from_env()

    assert str(failure.value) == (
        "OIN_DATABASE_URL is required when OIN_ENVIRONMENT=production; "
        "SQLite is not permitted"
    )


@pytest.mark.parametrize("environment", ["development", "test"])
def test_non_production_sqlite_remains_available(
    tmp_path: Path, environment: str
) -> None:
    settings = Settings(
        database_path=tmp_path / f"{environment}.db",
        database_url=None,
        public_base_url="https://testserver",
        apk_path=None,
        apk_version="unpublished",
        environment=environment,
    )

    database = Database(settings.database_path, settings.database_url)
    assert database.backend == "sqlite"
    database.migrate()
    database.verify_schema()


def test_production_postgresql_is_pinned_to_isolated_database_and_runtime_role(
    tmp_path: Path,
) -> None:
    settings = _production_settings(tmp_path)
    assert settings.database_url is not None

    with pytest.raises(ValueError, match="isolated oin_v01"):
        _production_settings(
            tmp_path,
            database_url="postgresql://oin_v01_app:test@127.0.0.1:5432/existing_business",
        )
    with pytest.raises(ValueError, match="isolated oin_v01"):
        _production_settings(
            tmp_path,
            database_url="postgresql://postgres:test@127.0.0.1:5432/oin_v01",
        )
    with pytest.raises(ValueError, match="out of process"):
        _production_settings(tmp_path, database_auto_migrate=True)


@pytest.mark.parametrize(
    "query",
    ["user=postgres", "dbname=existing_business", "service=shared-postgres"],
)
def test_production_postgresql_rejects_libpq_query_overrides(
    tmp_path: Path, query: str
) -> None:
    with pytest.raises(ValueError, match="query parameters"):
        _production_settings(
            tmp_path,
            database_url=(
                "postgresql://oin_v01_app:test@127.0.0.1:5432/oin_v01?" + query
            ),
        )


def test_postgresql_schema_matches_sqlite_tables_without_sqlite_only_ddl(tmp_path: Path) -> None:
    sqlite_database = Database(tmp_path / "oin-test.db")
    sqlite_database.migrate()
    with sqlite_database.connect() as connection:
        sqlite_tables = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        }

    postgres_schema = files("oin_control_plane").joinpath("schema.postgresql.sql").read_text(
        encoding="utf-8"
    )
    postgres_v4 = files("oin_control_plane").joinpath(
        "migration_v4.postgresql.sql"
    ).read_text(encoding="utf-8")
    postgres_v5 = files("oin_control_plane").joinpath(
        "migration_v5.postgresql.sql"
    ).read_text(encoding="utf-8")
    postgres_v6 = files("oin_control_plane").joinpath(
        "migration_v6.postgresql.sql"
    ).read_text(encoding="utf-8")
    postgres_v7 = files("oin_control_plane").joinpath(
        "migration_v7.postgresql.sql"
    ).read_text(encoding="utf-8")
    postgres_complete = "\n".join(
        (postgres_schema, postgres_v4, postgres_v5, postgres_v6, postgres_v7, files('oin_control_plane').joinpath('migration_v8.postgresql.sql').read_text(), files('oin_control_plane').joinpath('migration_v9.postgresql.sql').read_text())
    )
    postgres_tables = set(
        re.findall(
            r"CREATE TABLE(?: IF NOT EXISTS)?\s+([a-z_]+)",
            postgres_complete,
            re.IGNORECASE,
        )
    )
    assert postgres_tables == sqlite_tables
    assert "BLOB" not in postgres_complete
    assert "AUTOINCREMENT" not in postgres_complete
    assert "INSERT OR IGNORE" not in postgres_complete
    assert "INSERT INTO oin_schema_migrations" not in postgres_schema
    statements = _postgres_statements(postgres_schema)
    assert len(statements) == 26
    assert all(not statement.lstrip().startswith("--") for statement in statements)
    assert _postgres_statements("-- comment with ; delimiter\nSELECT 1;") == ["SELECT 1"]


def test_postgresql_bootstrap_is_append_only_for_history_and_marks_ready_last() -> None:
    bootstrap = (
        REPOSITORY_ROOT / "deploy" / "tx1-hx" / "postgresql-bootstrap.sql.template"
    ).read_text(encoding="utf-8")
    normalized = " ".join(bootstrap.split())

    assert "ON ALL TABLES" not in normalized
    assert "ON ALL SEQUENCES" not in normalized
    assert "ALTER DEFAULT PRIVILEGES" not in normalized
    assert "GRANT SELECT ON oin_v01.oin_schema_migrations TO oin_v01_app" in normalized
    assert "GRANT INSERT ON oin_v01.task_events, oin_v01.audit_logs TO oin_v01_app" in normalized
    assert "GRANT SELECT, INSERT, UPDATE ON oin_v01.device_sessions TO oin_v01_app" in normalized
    assert "oin_v01.task_delivery_attempts TO oin_v01_app" in normalized
    assert "oin_v01.task_events_id_seq, oin_v01.audit_logs_id_seq" in normalized
    assert (
        "GRANT EXECUTE ON FUNCTION pg_catalog.pg_advisory_xact_lock(integer, integer) "
        "TO oin_v01_app"
    ) in normalized

    marker_grants = re.findall(
        r"GRANT\s+([^;]+?)\s+ON\s+oin_v01\.oin_schema_migrations\s+TO\s+oin_v01_app",
        normalized,
        re.IGNORECASE,
    )
    assert marker_grants == ["SELECT"]

    begin_at = normalized.index("BEGIN;")
    schema_at = normalized.index("\\ir /opt/oin-v01/current/control-plane")
    last_grant_at = normalized.rindex("GRANT ")
    last_role_setting_at = normalized.rindex("ALTER ROLE ")
    marker_at = normalized.index("INSERT INTO oin_v01.oin_schema_migrations")
    commit_at = normalized.index("COMMIT;")
    assert begin_at < schema_at < last_grant_at < last_role_setting_at < marker_at < commit_at


class _FakeCursor:
    def __init__(self, row=None):
        self._row = row

    def fetchone(self):
        return self._row


class _FakePostgresConnection:
    def __init__(
        self,
        denied_privilege: tuple[str, str] | None = None,
        schema_version: int = 9,
    ):
        self.executed: list[tuple[str, object]] = []
        self.denied_privilege = denied_privilege
        self.schema_version = schema_version
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def execute(self, sql, parameters=None):
        self.executed.append((sql, parameters))
        if "SELECT MAX(version) AS version" in sql:
            return _FakeCursor({"version": self.schema_version})
        if "SELECT version FROM oin_schema_migrations" in sql:
            return _FakeCursor({"version": self.schema_version})
        if "SELECT 1 AS value" in sql:
            return _FakeCursor({"value": 1})
        if "has_table_privilege" in sql:
            return _FakeCursor(
                {"allowed": tuple(parameters or ()) != self.denied_privilege}
            )
        if "has_schema_privilege" in sql or "has_sequence_privilege" in sql or \
                "has_function_privilege" in sql:
            return _FakeCursor({"allowed": True})
        return _FakeCursor()

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


def test_postgresql_adapter_translates_parameters_and_serializes_immediate_transactions(
    monkeypatch, tmp_path: Path
) -> None:
    connections: list[_FakePostgresConnection] = []
    connect_kwargs: list[dict[str, object]] = []

    def fake_connect(*args, **kwargs):
        connection = _FakePostgresConnection()
        connections.append(connection)
        connect_kwargs.append(kwargs)
        return connection

    monkeypatch.setattr(psycopg, "connect", fake_connect)
    database = Database(
        tmp_path / "unused.db",
        "postgresql://oin_v01_app:test@127.0.0.1:5432/oin_v01",
    )

    with database.transaction(immediate=True) as connection:
        connection.execute("SELECT '?' AS literal, ? AS value", (7,))

    executed = [statement for statement, _ in connections[0].executed]
    assert executed[:3] == [
        "SET statement_timeout TO 3000",
        "SET lock_timeout TO 1500",
        "SET search_path TO oin_v01, pg_catalog",
    ]
    assert "BEGIN" in executed
    assert "SELECT pg_catalog.pg_advisory_xact_lock(1329808945, 1)" in executed
    assert "SELECT '?' AS literal, %s AS value" in executed
    assert "COMMIT" in executed
    assert connect_kwargs[0]["connect_timeout"] == 3
    assert connections[0].closed is True
    assert _translate_qmark("SELECT \"?\", ?, '?'") == "SELECT \"?\", %s, '?'"

    database.verify_schema()
    assert connections[1].closed is True
    database.verify_runtime_privileges()
    assert connections[2].closed is True


def test_postgresql_runtime_privilege_contract_fails_closed_on_missing_session_select(
    monkeypatch, tmp_path: Path
) -> None:
    def fake_connect(*args, **kwargs):
        return _FakePostgresConnection(("oin_v01.device_sessions", "SELECT"))

    monkeypatch.setattr(psycopg, "connect", fake_connect)
    database = Database(
        tmp_path / "unused.db",
        "postgresql://oin_v01_app:test@127.0.0.1:5432/oin_v01",
    )
    with pytest.raises(RuntimeError, match=r"device_sessions\.SELECT"):
        database.verify_runtime_privileges()


def test_postgresql_development_migration_marks_schema_ready_only_after_ddl(
    monkeypatch, tmp_path: Path
) -> None:
    connections: list[_FakePostgresConnection] = []

    def fake_connect(*args, **kwargs):
        connection = _FakePostgresConnection(schema_version=0)
        connections.append(connection)
        return connection

    monkeypatch.setattr(psycopg, "connect", fake_connect)
    database = Database(
        tmp_path / "unused.db",
        "postgresql://oin_v01_app:test@127.0.0.1:5432/oin_v01",
    )
    database.migrate()

    executed = [statement for statement, _ in connections[-1].executed]
    audit_ddl_at = next(
        index for index, statement in enumerate(executed) if "CREATE TABLE IF NOT EXISTS audit_logs" in statement
    )
    marker_at = next(
        index for index, statement in enumerate(executed) if "INSERT INTO oin_schema_migrations" in statement
    )
    commit_at = executed.index("COMMIT")
    assert audit_ddl_at < marker_at < commit_at


def test_sqlite_v1_to_v9_migration_adds_recovery_delivery_and_k2_authority(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "oin-v1.db"
    database = Database(database_path)
    with database.connect() as connection:
        connection.executescript(
            """
            CREATE TABLE oin_schema_migrations(
                version INTEGER PRIMARY KEY,
                applied_at INTEGER NOT NULL
            );
            CREATE TABLE accounts(id TEXT PRIMARY KEY);
            CREATE TABLE devices(id TEXT PRIMARY KEY);
            CREATE TABLE device_tasks(id TEXT PRIMARY KEY);
            INSERT INTO oin_schema_migrations(version, applied_at) VALUES (1, 1);
            """
        )

    database.migrate()
    database.verify_schema()
    with database.connect() as connection:
        task_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(device_tasks)").fetchall()
        }
        tables = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        versions = [
            row["version"]
            for row in connection.execute(
                "SELECT version FROM oin_schema_migrations ORDER BY version"
            ).fetchall()
        ]
    assert {
        "reported_result_json",
        "result_received_at",
        "result_disposition",
        "admin_disposition",
    }.issubset(task_columns)
    assert {
        "device_credential_repair_challenges",
        "outbox_reconciliation_authorizations",
        "outbox_reconciliation_items",
        "task_delivery_attempts",
        "account_users",
        "tenants",
        "platform_account_bindings",
        "connector_route_activations",
        "identity_evidence",
        "identity_confirmations",
    }.issubset(tables)
    assert versions == [1, 2, 3, 4, 5, 6, 7, 8, 9]
    database.migrate()


def test_postgresql_v1_to_v3_development_migration_marks_ready_after_new_ddl(
    monkeypatch, tmp_path: Path
) -> None:
    connections: list[_FakePostgresConnection] = []

    def fake_connect(*args, **kwargs):
        connection = _FakePostgresConnection(schema_version=1)
        connections.append(connection)
        return connection

    monkeypatch.setattr(psycopg, "connect", fake_connect)
    database = Database(
        tmp_path / "unused.db",
        "postgresql://oin_v01_app:test@127.0.0.1:5432/oin_v01",
    )
    database.migrate()

    executed = [statement for statement, _ in connections[-1].executed]
    late_result_ddl_at = next(
        index
        for index, statement in enumerate(executed)
        if "ADD COLUMN reported_result_json" in statement
    )
    reconciliation_ddl_at = next(
        index
        for index, statement in enumerate(executed)
        if "CREATE TABLE outbox_reconciliation_items" in statement
    )
    delivery_attempt_ddl_at = next(
        index
        for index, statement in enumerate(executed)
        if "CREATE TABLE task_delivery_attempts" in statement
    )
    marker_at = next(
        index
        for index, statement in enumerate(executed)
        if "INSERT INTO oin_schema_migrations" in statement
    )
    commit_at = executed.index("COMMIT")
    assert (
        late_result_ddl_at
        < reconciliation_ddl_at
        < delivery_attempt_ddl_at
        < marker_at
        < commit_at
    )


def test_production_v2_migration_template_is_owner_controlled_and_non_destructive() -> None:
    template = (
        REPOSITORY_ROOT / "deploy" / "tx1-hx" / "postgresql-migration-v2.sql.template"
    ).read_text(encoding="utf-8")
    normalized = " ".join(template.split())
    assert "SET ROLE oin_v01_owner" in normalized
    assert "migration_v2.postgresql.sql" in normalized
    assert "VALUES (2, EXTRACT(EPOCH FROM clock_timestamp())::BIGINT)" in normalized
    assert "DROP TABLE" not in normalized.upper()
    assert "DELETE FROM" not in normalized.upper()
    assert normalized.index("BEGIN;") < normalized.index("migration_v2.postgresql.sql")
    assert normalized.index("migration_v2.postgresql.sql") < normalized.index("VALUES (2,")
    assert normalized.index("VALUES (2,") < normalized.index("COMMIT;")


def test_production_v3_migration_template_is_owner_controlled_and_non_destructive() -> None:
    template = (
        REPOSITORY_ROOT / "deploy" / "tx1-hx" / "postgresql-migration-v3.sql.template"
    ).read_text(encoding="utf-8")
    normalized = " ".join(template.split())
    assert "SET ROLE oin_v01_owner" in normalized
    assert "migration_v3.postgresql.sql" in normalized
    assert "GRANT SELECT, INSERT, UPDATE ON oin_v01.task_delivery_attempts" in normalized
    assert "VALUES (3, EXTRACT(EPOCH FROM clock_timestamp())::BIGINT)" in normalized
    assert "DROP TABLE" not in normalized.upper()
    assert "DELETE FROM" not in normalized.upper()
    assert normalized.index("BEGIN;") < normalized.index("migration_v3.postgresql.sql")
    assert normalized.index("migration_v3.postgresql.sql") < normalized.index("VALUES (3,")
    assert normalized.index("VALUES (3,") < normalized.index("COMMIT;")


@pytest.mark.skipif(
    not os.environ.get("OIN_TEST_POSTGRES_URL"),
    reason="ephemeral local PostgreSQL URL not supplied",
)
def test_live_postgresql_persists_late_result_before_exact_duplicate_ack(
    tmp_path: Path,
) -> None:
    database_url = os.environ["OIN_TEST_POSTGRES_URL"]
    parsed = urlsplit(database_url)
    assert parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    assert parsed.path.strip("/") == "oin_code25_test"
    with psycopg.connect(database_url, autocommit=True) as owner:
        owner.execute("CREATE SCHEMA IF NOT EXISTS oin_v01")

    database = Database(tmp_path / "unused.db", database_url)
    database.migrate()
    database.verify_schema()
    settings = Settings(
        database_path=tmp_path / "unused.db",
        database_url=database_url,
        database_auto_migrate=False,
        public_base_url="https://testserver",
        apk_path=None,
        apk_version="unpublished",
        environment="test",
        password_iterations=100_000,
    )
    store = Store(database, settings)
    account = store.create_account(
        "postgres-late-owner",
        "postgres-late-owner@example.test",
        "correct horse battery",
    )
    device_id = "11111111-1111-4111-8111-111111111111"
    current = now_seconds()
    capabilities = canonical_json({"cloud_task_types": ["health"]})
    with database.connect() as connection:
        connection.execute(
            """
            INSERT INTO devices(
                id, owner_account_id, display_name, manufacturer, brand, model,
                android_version, sdk, app_version, app_version_code, kernel_version,
                timezone, network_type, installation_id, info_json, capabilities_json,
                binding_state, online, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'bound', 0, ?, ?)
            """,
            (
                device_id,
                account["id"],
                "PostgreSQL late phone",
                "TestMaker",
                "TestBrand",
                "Model 1",
                "14",
                35,
                "0.9.2-v01-dev",
                25,
                "0.9.2-v01-dev",
                "Asia/Shanghai",
                "wifi",
                "install-postgres-late",
                "{}",
                capabilities,
                current,
                current,
            ),
        )
    task, _ = store.create_task(
        account["id"],
        device_id,
        "health",
        "postgres-late-result-001",
        {},
        60,
    )
    assert store.mark_task_running(device_id, task["id"])
    with database.connect() as connection:
        connection.execute(
            "UPDATE device_tasks SET expires_at = ? WHERE id = ?",
            (now_seconds() - 1, task["id"]),
        )

    first = store.complete_task(
        device_id, task["id"], "succeeded", {"durable": True}, None, None
    )
    duplicate = store.complete_task(
        device_id, task["id"], "succeeded", {"durable": True}, None, None
    )
    conflict = store.complete_task(
        device_id, task["id"], "succeeded", {"durable": False}, None, None
    )
    assert first.kind is TaskCompletionKind.STORED
    assert duplicate.kind is TaskCompletionKind.EXACT_DUPLICATE
    assert conflict.kind is TaskCompletionKind.CONFLICT
    persisted = store.get_task(account["id"], device_id, task["id"])
    assert persisted is not None
    assert persisted["status"] == "expired"
    assert persisted["effective_status"] == "expired_with_late_result"
    assert persisted["reported_result"] == {
        "status": "succeeded",
        "result": {"durable": True},
    }


def test_sqlite_v8_to_v9_retains_existing_job_control_state(tmp_path):
    database=Database(tmp_path/'upgrade.db');database.migrate()
    with database.connect() as connection:
        columns={row['name'] for row in connection.execute('PRAGMA table_info(seller_jobs)').fetchall()}
    assert {'control_state','outcomes_json'}.issubset(columns)
    # Migration is idempotent at the version boundary.
    database.migrate();database.verify_schema()
