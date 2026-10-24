"""Fresh isolated PostgreSQL provisioning; run as postgres, secrets on stdin.

Never reuse/drop an existing database or role. Product schemas and table grants
come from the exact staged application, not an outdated deployment template.
"""
import json
import sys
from importlib.resources import files

import psycopg
from psycopg import sql
from oin_control_plane.database import (
    CURRENT_SCHEMA_VERSION, RUNTIME_TABLE_PRIVILEGES, RUNTIME_SEQUENCE_PRIVILEGES,
    _postgres_statements,
)

password=json.load(sys.stdin)['password']
with psycopg.connect('dbname=postgres',autocommit=True) as c:
    roles=c.execute("SELECT rolname FROM pg_roles WHERE rolname IN ('oin_v01_app','oin_v01_owner')").fetchall()
    databases=c.execute("SELECT datname FROM pg_database WHERE datname='oin_v01'").fetchall()
    if roles or databases: raise SystemExit('Existing OIN roles/database: inspect, do not overwrite')
    with c.transaction():
        c.execute('CREATE ROLE oin_v01_owner NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE')
        c.execute(sql.SQL('CREATE ROLE oin_v01_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD {}').format(sql.Literal(password)))
    c.execute('CREATE DATABASE oin_v01 OWNER oin_v01_owner')
    c.execute('REVOKE ALL ON DATABASE oin_v01 FROM PUBLIC')
    c.execute('GRANT CONNECT ON DATABASE oin_v01 TO oin_v01_app')
with psycopg.connect('dbname=oin_v01',autocommit=True) as c:
    with c.transaction():
        c.execute('REVOKE ALL ON SCHEMA public FROM PUBLIC')
        c.execute('SET ROLE oin_v01_owner')
        c.execute('CREATE SCHEMA oin_v01 AUTHORIZATION oin_v01_owner')
        c.execute('SET search_path TO oin_v01, pg_catalog')
        resources=files('oin_control_plane')
        scripts=[resources.joinpath('schema.postgresql.sql').read_text()]
        scripts.extend(resources.joinpath(f'migration_v{v}.postgresql.sql').read_text() for v in range(4,CURRENT_SCHEMA_VERSION+1))
        for script in scripts:
            for statement in _postgres_statements(script): c.execute(statement)
        c.execute('GRANT USAGE ON SCHEMA oin_v01 TO oin_v01_app')
        for table,privileges in RUNTIME_TABLE_PRIVILEGES:
            c.execute(sql.SQL('GRANT {} ON oin_v01.{} TO oin_v01_app').format(sql.SQL(','.join(privileges)),sql.Identifier(table)))
        for sequence in RUNTIME_SEQUENCE_PRIVILEGES:
            c.execute(sql.SQL('GRANT USAGE ON SEQUENCE oin_v01.{} TO oin_v01_app').format(sql.Identifier(sequence)))
    c.execute('RESET ROLE')
    c.execute('ALTER ROLE oin_v01_app IN DATABASE oin_v01 SET search_path=oin_v01,pg_catalog')
    c.execute("ALTER ROLE oin_v01_app IN DATABASE oin_v01 SET idle_in_transaction_session_timeout='30s'")
print(f'OIN isolated database initialized: schema {CURRENT_SCHEMA_VERSION}')
