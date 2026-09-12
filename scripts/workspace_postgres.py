"""Provision the private PostgreSQL schema, export it, or restore an empty store.

Connection strings and passwords come from environment variables, never CLI
arguments. `migrate` and PostgreSQL `restore` use WORKSPACE_MIGRATION_DATABASE_URL.
Creating the runtime role additionally needs WORKSPACE_RUNTIME_DB_PASSWORD.
`backup` uses the application's WORKSPACE_DATABASE_URL (or development SQLite).
Backups are portable JSON, including suppressions, drafts, events and original
IDs. Restore never overwrites a populated workspace and is transactional.
"""

import argparse
import json
import os
import sqlite3
import sys
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

import psycopg2
from psycopg2 import sql
from pydantic import ValidationError

from backend.app.config import settings
from backend.app.prospecting import DraftUpdate, LeadUpdate, Profile, validate_email
from backend.app.workspace_store import (
    POSTGRES_RUNTIME_ROLE,
    POSTGRES_SCHEMA,
    WORKSPACE_TABLES,
    WRITER_LOCK,
    WorkspaceStoreError,
    connect,
    postgres_connect,
    postgres_options,
)
from scripts.workspace_backup import check_workspace

MIGRATION = Path(__file__).resolve().parents[1] / "migrations" / "004_workspace.sql"
COLUMNS = {
    "profile": ("id", "data"),
    "leads": (
        "id",
        "company_name",
        "siren",
        "contact_name",
        "email",
        "website",
        "city",
        "activity",
        "source",
        "notes",
        "status",
        "qualified",
        "created_at",
        "updated_at",
    ),
    "suppressions": ("email", "created_at"),
    "campaigns": ("id", "name", "profile", "created_at"),
    "drafts": ("id", "campaign_id", "lead_id", "step", "subject", "body", "footer", "sent_at"),
    "events": ("id", "lead_id", "kind", "detail", "created_at"),
}


def migrate(url: str, runtime_password: str | None = None) -> None:
    """Create only the dedicated workspace; never reuse the legacy public tables."""
    options = postgres_options(
        url,
        production=settings.app_env == "production",
        root_cert=settings.workspace_database_ssl_root_cert,
    )
    try:
        with closing(psycopg2.connect(url, **options)) as conn, conn, conn.cursor() as cursor:
            cursor.execute("SET LOCAL statement_timeout = '30000ms'")
            cursor.execute("SET LOCAL lock_timeout = '5000ms'")
            cursor.execute("SELECT pg_advisory_xact_lock(%s,%s)", WRITER_LOCK)
            cursor.execute("SELECT current_user")
            owner = cursor.fetchone()[0]
            if owner == POSTGRES_RUNTIME_ROLE:
                raise ValueError(
                    "La migration exige le propriétaire de la base, pas outreached_app."
                )
            cursor.execute(
                "SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname=%s",
                (POSTGRES_SCHEMA,),
            )
            existing = cursor.fetchone()
            if existing and existing[0] != owner:
                raise ValueError(
                    "Le schéma existant appartient à un autre rôle ; migration refusée."
                )
            cursor.execute(
                "SELECT oid,rolsuper,rolcreatedb,rolcreaterole,rolbypassrls,rolreplication FROM pg_roles WHERE rolname=%s",
                (POSTGRES_RUNTIME_ROLE,),
            )
            role = cursor.fetchone()
            if role:
                cursor.execute("SELECT 1 FROM pg_auth_members WHERE member=%s", (role[0],))
                if any(role[1:]) or cursor.fetchone():
                    raise ValueError(
                        "Le rôle existant outreached_app possède des privilèges excessifs."
                    )
            else:
                if (
                    not runtime_password
                    or len(runtime_password) < 32
                    or any(c.isspace() for c in runtime_password)
                ):
                    raise ValueError(
                        "WORKSPACE_RUNTIME_DB_PASSWORD doit contenir au moins 32 caractères sans espace."
                    )
                cursor.execute(
                    "CREATE ROLE outreached_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                    "NOINHERIT NOBYPASSRLS NOREPLICATION PASSWORD %s",
                    (runtime_password,),
                )
            cursor.execute(MIGRATION.read_text())
            cursor.execute("SELECT version FROM outreached_workspace.schema_version WHERE id=1")
            if cursor.fetchone() != (1,):
                raise ValueError("Version de schéma inattendue ; migration refusée.")
            cursor.execute(
                "SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                "JOIN pg_roles r ON r.oid=c.relowner "
                "WHERE n.nspname=%s AND r.rolname=%s LIMIT 1",
                (POSTGRES_SCHEMA, POSTGRES_RUNTIME_ROLE),
            )
            if cursor.fetchone():
                raise ValueError("Le rôle runtime ne doit posséder aucun objet du schéma privé.")
            # Reapplying a migration repairs ACL drift; GRANT alone would leave
            # old DELETE/TRUNCATE/DDL permissions or delegated grants intact.
            cursor.execute("REVOKE ALL ON SCHEMA outreached_workspace FROM outreached_app CASCADE")
            cursor.execute(
                "REVOKE ALL ON ALL TABLES IN SCHEMA outreached_workspace FROM outreached_app CASCADE"
            )
            cursor.execute(
                "REVOKE ALL ON ALL SEQUENCES IN SCHEMA outreached_workspace FROM outreached_app CASCADE"
            )
            cursor.execute("REVOKE ALL ON ALL TABLES IN SCHEMA outreached_workspace FROM PUBLIC")
            cursor.execute("REVOKE ALL ON ALL SEQUENCES IN SCHEMA outreached_workspace FROM PUBLIC")
            # Supabase-specific roles may exist; Neon and ordinary PostgreSQL
            # do not require them. Neither role may access this private schema.
            for role_name in ("anon", "authenticated"):
                cursor.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role_name,))
                if cursor.fetchone():
                    cursor.execute(
                        sql.SQL("REVOKE ALL ON SCHEMA outreached_workspace FROM {}").format(
                            sql.Identifier(role_name)
                        )
                    )
                    cursor.execute(
                        sql.SQL(
                            "REVOKE ALL ON ALL TABLES IN SCHEMA outreached_workspace FROM {}"
                        ).format(sql.Identifier(role_name))
                    )
                    cursor.execute(
                        sql.SQL(
                            "REVOKE ALL ON ALL SEQUENCES IN SCHEMA outreached_workspace FROM {}"
                        ).format(sql.Identifier(role_name))
                    )
            cursor.execute("GRANT USAGE ON SCHEMA outreached_workspace TO outreached_app")
            cursor.execute("GRANT SELECT ON outreached_workspace.schema_version TO outreached_app")
            for table in WORKSPACE_TABLES:
                relation = sql.Identifier(POSTGRES_SCHEMA, table)
                cursor.execute(sql.SQL("ALTER TABLE {} ENABLE ROW LEVEL SECURITY").format(relation))
                cursor.execute(
                    sql.SQL("DROP POLICY IF EXISTS workspace_backend ON {}").format(relation)
                )
                cursor.execute(
                    sql.SQL(
                        "CREATE POLICY workspace_backend ON {} TO outreached_app USING (true) WITH CHECK (true)"
                    ).format(relation)
                )
                cursor.execute(
                    sql.SQL("GRANT SELECT,INSERT,UPDATE ON {} TO outreached_app").format(relation)
                )
            cursor.execute(
                "GRANT USAGE,SELECT ON SEQUENCE outreached_workspace.events_id_seq TO outreached_app"
            )
    except psycopg2.Error:
        raise WorkspaceStoreError(
            "La migration PostgreSQL a échoué ; aucune modification validée."
        ) from None


def snapshot(connection) -> dict:
    return {
        "version": 1,
        "exported_at": datetime.now(UTC).isoformat(),
        "data": {
            table: [dict(row) for row in connection.execute(f"SELECT * FROM {table}").fetchall()]
            for table in WORKSPACE_TABLES
        },
    }


def backup(destination: Path) -> dict[str, int]:
    with connect(write=False) as conn:
        data = snapshot(conn)
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return {table: len(rows) for table, rows in data["data"].items()}


def read_backup(source: Path) -> dict:
    if source.suffix.lower() == ".json":
        data = json.loads(source.read_text(encoding="utf-8"))
    else:
        with closing(
            sqlite3.connect(source.resolve(strict=True).as_uri() + "?mode=ro", uri=True)
        ) as conn:
            check_workspace(conn)
            conn.row_factory = sqlite3.Row
            conn.execute("BEGIN")
            data = snapshot(conn)
    if (
        not isinstance(data, dict)
        or data.get("version") != 1
        or not isinstance(data.get("data"), dict)
    ):
        raise ValueError("Format de sauvegarde invalide.")
    rows = data["data"]
    if set(rows) != set(WORKSPACE_TABLES):
        raise ValueError("La sauvegarde doit inclure les six tables, dont les oppositions.")
    for table, values in rows.items():
        if not isinstance(values, list) or any(
            not isinstance(row, dict) or set(row) != set(COLUMNS[table]) for row in values
        ):
            raise ValueError("Colonnes ou lignes de sauvegarde invalides.")
    for lead in rows["leads"]:
        if lead["qualified"] not in (True, False, 0, 1):
            raise ValueError("Qualification invalide dans la sauvegarde.")
        lead["qualified"] = bool(lead["qualified"])
    try:
        for profile in rows["profile"]:
            Profile.model_validate_json(profile["data"])
        for campaign in rows["campaigns"]:
            Profile.model_validate_json(campaign["profile"])
        for lead in rows["leads"]:
            values = {key: lead[key] for key in LeadUpdate.model_fields}
            if LeadUpdate.model_validate(values).model_dump() != values:
                raise ValueError
        for suppression in rows["suppressions"]:
            if (
                not suppression["email"]
                or validate_email(suppression["email"]) != suppression["email"]
            ):
                raise ValueError
        for draft in rows["drafts"]:
            DraftUpdate(subject=draft["subject"], body=draft["body"])
        for table in ("leads", "suppressions", "campaigns", "events"):
            for row in rows[table]:
                datetime.fromisoformat(row["created_at"])
        for lead in rows["leads"]:
            datetime.fromisoformat(lead["updated_at"])
        for draft in rows["drafts"]:
            if (
                draft["sent_at"] is not None
                and datetime.fromisoformat(draft["sent_at"]).tzinfo is None
            ):
                raise ValueError
    except (ValidationError, ValueError, TypeError, AttributeError):
        raise ValueError("La sauvegarde contient des données métier invalides.") from None
    return rows


def restore(source: Path, *, postgres_url: str | None = None) -> dict[str, int]:
    rows = read_backup(source)
    context = postgres_connect(postgres_url) if postgres_url else connect()
    with context as conn:
        if getattr(conn, "is_postgres", False) and (
            conn.execute("SELECT current_user AS role").fetchone()["role"] == POSTGRES_RUNTIME_ROLE
        ):
            raise ValueError("La restauration PostgreSQL exige les identifiants de migration.")
        if any(
            conn.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone() for table in WORKSPACE_TABLES
        ):
            raise ValueError("Restauration refusée : l’espace contient déjà des données.")
        for table in WORKSPACE_TABLES:
            columns = COLUMNS[table]
            statement = f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})"
            for row in rows[table]:
                conn.execute(statement, tuple(row[column] for column in columns))
        if getattr(conn, "is_postgres", False):
            conn.execute(
                "SELECT setval('outreached_workspace.events_id_seq', "
                "COALESCE(MAX(id),1), MAX(id) IS NOT NULL) FROM events"
            )
    return {table: len(values) for table, values in rows.items()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("migrate", "backup", "restore"))
    parser.add_argument("file", nargs="?", type=Path)
    args = parser.parse_args()
    try:
        if args.operation == "migrate":
            url = os.environ.get("WORKSPACE_MIGRATION_DATABASE_URL")
            if not url or args.file:
                raise ValueError("migrate exige WORKSPACE_MIGRATION_DATABASE_URL et aucun fichier.")
            migrate(url, os.environ.get("WORKSPACE_RUNTIME_DB_PASSWORD"))
            print("Migration du schéma privé vérifiée ; aucun secret affiché.")
        elif not args.file:
            raise ValueError("Un chemin de fichier est requis pour backup/restore.")
        elif args.operation == "backup":
            print(f"Sauvegarde cohérente créée : {backup(args.file)}")
        else:
            url = os.environ.get("WORKSPACE_MIGRATION_DATABASE_URL")
            if settings.workspace_database_url and not url:
                raise ValueError(
                    "La restauration PostgreSQL exige WORKSPACE_MIGRATION_DATABASE_URL."
                )
            print(f"Restauration atomique vérifiée : {restore(args.file, postgres_url=url)}")
    except (RuntimeError, ValueError, OSError, sqlite3.Error):
        # Do not echo DSNs, database errors, CSV/contact values, or passwords.
        print(
            "Opération refusée ou échouée. Vérifiez les variables, droits, schéma et fichier ; aucun secret affiché.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
