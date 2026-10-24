from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from importlib.resources import files
from pathlib import Path
from typing import Any, Iterator, Sequence


CURRENT_SCHEMA_VERSION = 9
POSTGRES_SCHEMA = "oin_v01"
RUNTIME_TABLE_PRIVILEGES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("oin_schema_migrations", ("SELECT",)),
    ("accounts", ("SELECT", "INSERT")),
    ("preauth_sessions", ("SELECT", "INSERT", "DELETE")),
    ("web_sessions", ("SELECT", "INSERT", "DELETE")),
    ("mobile_sessions", ("SELECT", "INSERT", "UPDATE", "DELETE")),
    ("account_users", ("SELECT", "INSERT")),
    ("tenants", ("SELECT", "INSERT", "UPDATE")),
    ("merchants", ("SELECT", "INSERT", "UPDATE")),
    ("memberships", ("SELECT", "INSERT", "UPDATE")),
    ("platform_shops", ("SELECT", "INSERT", "UPDATE")),
    ("platform_principals", ("SELECT", "INSERT", "UPDATE")),
    ("platform_principal_shops", ("SELECT", "INSERT", "DELETE")),
    ("connector_enrollment_authorizations", ("SELECT", "INSERT", "UPDATE")),
    ("connector_registration_challenges", ("SELECT", "INSERT", "UPDATE")),
    ("web_connectors", ("SELECT", "INSERT", "UPDATE")),
    ("connector_profiles", ("SELECT", "INSERT", "UPDATE")),
    ("platform_account_bindings", ("SELECT", "INSERT", "UPDATE")),
    ("connector_route_activations", ("SELECT", "INSERT", "UPDATE")),
    ("identity_evidence", ("SELECT", "INSERT")),
    ("identity_confirmations", ("SELECT", "INSERT")),
    ("operation_idempotency", ("SELECT", "INSERT")),
    ("enrollment_challenges", ("SELECT", "INSERT", "UPDATE", "DELETE")),
    ("device_credential_repair_challenges", ("SELECT", "INSERT", "UPDATE", "DELETE")),
    ("device_revocation_challenges", ("SELECT", "INSERT", "UPDATE", "DELETE")),
    ("devices", ("SELECT", "INSERT", "UPDATE")),
    ("device_credentials", ("SELECT", "INSERT", "UPDATE")),
    ("device_tasks", ("SELECT", "INSERT", "UPDATE")),
    ("task_delivery_attempts", ("SELECT", "INSERT", "UPDATE")),
    ("device_sessions", ("SELECT", "INSERT", "UPDATE")),
    ("outbox_reconciliation_authorizations", ("SELECT", "INSERT", "UPDATE", "DELETE")),
    ("outbox_reconciliation_items", ("SELECT", "INSERT", "UPDATE", "DELETE")),
    ("task_events", ("INSERT",)),
    ("audit_logs", ("INSERT",)),
    ("seller_items", ("SELECT", "INSERT", "UPDATE")),
    ("seller_recipes", ("SELECT", "INSERT", "UPDATE")),
    ("seller_jobs", ("SELECT", "INSERT", "UPDATE")),
    ("seller_schedules", ("SELECT", "INSERT", "UPDATE")),
)
RUNTIME_SEQUENCE_PRIVILEGES = ("task_events_id_seq", "audit_logs_id_seq")


class IntegrityError(RuntimeError):
    """Backend-neutral uniqueness or referential-integrity failure."""


def _translate_qmark(sql: str) -> str:
    """Translate SQLite qmark parameters without touching quoted question marks."""

    translated: list[str] = []
    in_single_quote = False
    in_double_quote = False
    index = 0
    while index < len(sql):
        character = sql[index]
        if character == "'" and not in_double_quote:
            translated.append(character)
            if in_single_quote and index + 1 < len(sql) and sql[index + 1] == "'":
                translated.append("'")
                index += 2
                continue
            in_single_quote = not in_single_quote
        elif character == '"' and not in_single_quote:
            translated.append(character)
            if in_double_quote and index + 1 < len(sql) and sql[index + 1] == '"':
                translated.append('"')
                index += 2
                continue
            in_double_quote = not in_double_quote
        elif character == "?" and not in_single_quote and not in_double_quote:
            translated.append("%s")
        else:
            translated.append(character)
        index += 1
    return "".join(translated)


def _postgres_statements(script: str) -> list[str]:
    """Split the controlled schema file after removing its full-line comments."""

    uncommented = "\n".join(
        line for line in script.splitlines() if not line.lstrip().startswith("--")
    )
    return [statement.strip() for statement in uncommented.split(";") if statement.strip()]


class Connection:
    def __init__(self, raw: Any, backend: str):
        self._raw = raw
        self.backend = backend

    def execute(self, sql: str, parameters: Sequence[Any] | None = None):
        statement = _translate_qmark(sql) if self.backend == "postgresql" else sql
        try:
            if parameters is None:
                return self._raw.execute(statement)
            return self._raw.execute(statement, parameters)
        except sqlite3.IntegrityError as exc:
            raise IntegrityError("database_integrity_error") from exc
        except BaseException as exc:
            if self.backend == "postgresql":
                from psycopg import IntegrityError as PsycopgIntegrityError

                if isinstance(exc, PsycopgIntegrityError):
                    raise IntegrityError("database_integrity_error") from exc
            raise

    def executescript(self, script: str) -> None:
        if self.backend != "sqlite":
            raise RuntimeError("executescript is SQLite-only")
        self._raw.executescript(script)

    def commit(self) -> None:
        self._raw.commit()

    def rollback(self) -> None:
        self._raw.rollback()

    def close(self) -> None:
        self._raw.close()

    def __enter__(self) -> "Connection":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()


class Database:
    """Small synchronous adapter used by the single-process V01 control plane."""

    def __init__(
        self,
        path: Path,
        database_url: str | None = None,
        *,
        connect_timeout_seconds: int = 3,
        statement_timeout_ms: int = 3_000,
        lock_timeout_ms: int = 1_500,
    ):
        self.path = path
        self.database_url = database_url
        self.connect_timeout_seconds = connect_timeout_seconds
        self.statement_timeout_ms = statement_timeout_ms
        self.lock_timeout_ms = lock_timeout_ms
        self.backend = "postgresql" if database_url else "sqlite"
        if self.backend == "sqlite":
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> Connection:
        if self.backend == "sqlite":
            raw = sqlite3.connect(self.path, timeout=10.0, isolation_level=None)
            raw.row_factory = sqlite3.Row
            raw.execute("PRAGMA foreign_keys = ON")
            raw.execute("PRAGMA busy_timeout = 10000")
            return Connection(raw, self.backend)

        import psycopg
        from psycopg.rows import dict_row

        raw = psycopg.connect(
            self.database_url,
            autocommit=True,
            connect_timeout=self.connect_timeout_seconds,
            row_factory=dict_row,
        )
        raw.execute(f"SET statement_timeout TO {int(self.statement_timeout_ms)}")
        raw.execute(f"SET lock_timeout TO {int(self.lock_timeout_ms)}")
        raw.execute(f"SET search_path TO {POSTGRES_SCHEMA}, pg_catalog")
        return Connection(raw, self.backend)

    def migrate(self) -> None:
        resource = "schema.postgresql.sql" if self.backend == "postgresql" else "schema.sql"
        schema = files("oin_control_plane").joinpath(resource).read_text(encoding="utf-8")
        current_version = self._current_schema_version()
        if current_version > CURRENT_SCHEMA_VERSION:
            raise RuntimeError(
                f"{self.backend} schema version {current_version} is newer than supported "
                f"version {CURRENT_SCHEMA_VERSION}"
            )
        if current_version == CURRENT_SCHEMA_VERSION:
            return
        if self.backend == "sqlite":
            if current_version == 0:
                with self.connect() as connection:
                    connection.execute("PRAGMA journal_mode = WAL")
                    connection.executescript(schema)
                current_version = 3
            if current_version not in {1, 2, 3, 4, 5, 6, 7, 8}:
                raise RuntimeError(f"unsupported SQLite schema migration from {current_version}")
            for version in range(current_version + 1, CURRENT_SCHEMA_VERSION + 1):
                migration = files("oin_control_plane").joinpath(
                    f"migration_v{version}.sql"
                ).read_text(encoding="utf-8")
                with self.transaction(immediate=True) as connection:
                    for statement in _postgres_statements(migration):
                        connection.execute(statement)
            return

        # Production uses an out-of-process owner migration. This path exists for
        # isolated development/integration databases only.
        if current_version == 0:
            statements = _postgres_statements(schema)
            for version in range(4, CURRENT_SCHEMA_VERSION + 1):
                migration = files("oin_control_plane").joinpath(
                    f"migration_v{version}.postgresql.sql"
                ).read_text(encoding="utf-8")
                statements.extend(_postgres_statements(migration))
        elif current_version in {1, 2, 3, 4, 5, 6, 7, 8}:
            statements = []
            for version in range(current_version + 1, CURRENT_SCHEMA_VERSION + 1):
                migration = files("oin_control_plane").joinpath(
                    f"migration_v{version}.postgresql.sql"
                ).read_text(encoding="utf-8")
                statements.extend(_postgres_statements(migration))
        else:
            raise RuntimeError(
                f"unsupported PostgreSQL schema migration from {current_version}"
            )
        with self.transaction(immediate=True) as connection:
            for statement in statements:
                connection.execute(statement)
            connection.execute(
                """
                INSERT INTO oin_schema_migrations(version, applied_at)
                VALUES (?, EXTRACT(EPOCH FROM clock_timestamp())::BIGINT)
                ON CONFLICT (version) DO NOTHING
                """,
                (CURRENT_SCHEMA_VERSION,),
            )

    def _current_schema_version(self) -> int:
        try:
            with self.connect() as connection:
                row = connection.execute(
                    "SELECT MAX(version) AS version FROM oin_schema_migrations"
                ).fetchone()
        except BaseException:
            return 0
        if not row or row["version"] is None:
            return 0
        return int(row["version"])

    def verify_schema(self) -> None:
        try:
            with self.connect() as connection:
                row = connection.execute(
                    "SELECT version FROM oin_schema_migrations WHERE version = ?",
                    (CURRENT_SCHEMA_VERSION,),
                ).fetchone()
        except BaseException as exc:
            raise RuntimeError(
                f"{self.backend} schema is not initialized; apply schema version "
                f"{CURRENT_SCHEMA_VERSION} before starting the service"
            ) from exc
        if row is None or row["version"] != CURRENT_SCHEMA_VERSION:
            raise RuntimeError(
                f"{self.backend} schema version {CURRENT_SCHEMA_VERSION} is required"
            )

    def verify_runtime_privileges(self) -> None:
        if self.backend != "postgresql":
            return
        missing: list[str] = []
        with self.connect() as connection:
            schema = connection.execute(
                "SELECT pg_catalog.has_schema_privilege(current_user, ?, 'USAGE') AS allowed",
                (POSTGRES_SCHEMA,),
            ).fetchone()
            if not schema or not schema["allowed"]:
                missing.append(f"schema:{POSTGRES_SCHEMA}.USAGE")
            for table, privileges in RUNTIME_TABLE_PRIVILEGES:
                qualified = f"{POSTGRES_SCHEMA}.{table}"
                for privilege in privileges:
                    row = connection.execute(
                        "SELECT pg_catalog.has_table_privilege(current_user, ?, ?) AS allowed",
                        (qualified, privilege),
                    ).fetchone()
                    if not row or not row["allowed"]:
                        missing.append(f"table:{qualified}.{privilege}")
            for sequence in RUNTIME_SEQUENCE_PRIVILEGES:
                qualified = f"{POSTGRES_SCHEMA}.{sequence}"
                row = connection.execute(
                    "SELECT pg_catalog.has_sequence_privilege(current_user, ?, 'USAGE') AS allowed",
                    (qualified,),
                ).fetchone()
                if not row or not row["allowed"]:
                    missing.append(f"sequence:{qualified}.USAGE")
            function = connection.execute(
                "SELECT pg_catalog.has_function_privilege("
                "current_user, 'pg_catalog.pg_advisory_xact_lock(integer,integer)', "
                "'EXECUTE') AS allowed"
            ).fetchone()
            if not function or not function["allowed"]:
                missing.append("function:pg_advisory_xact_lock(integer,integer).EXECUTE")
        if missing:
            raise RuntimeError(
                "postgresql runtime privilege contract is incomplete: " + ", ".join(missing)
            )

    @contextmanager
    def transaction(self, *, immediate: bool = False) -> Iterator[Connection]:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE" if self.backend == "sqlite" and immediate else "BEGIN")
            if self.backend == "postgresql" and immediate:
                # Preserve SQLite BEGIN IMMEDIATE's tiny-control-plane serialization
                # semantics across multiple server processes without granting DDL.
                connection.execute(
                    "SELECT pg_catalog.pg_advisory_xact_lock(1329808945, 1)"
                )
            yield connection
            if self.backend == "postgresql":
                connection.execute("COMMIT")
            else:
                connection.commit()
        except BaseException:
            try:
                if self.backend == "postgresql":
                    connection.execute("ROLLBACK")
                else:
                    connection.rollback()
            except BaseException:
                pass
            raise
        finally:
            connection.close()

    def ping(self) -> bool:
        with self.connect() as connection:
            row = connection.execute("SELECT 1 AS value").fetchone()
            return row is not None and row["value"] == 1
