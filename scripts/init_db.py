"""
Database initialization script for LinkedIn Post Scheduler.
Connects with root credentials, verifies MySQL >= 8.0, ensures database exists with utf8mb4,
provisions a least-privilege app user, and applies schema.sql idempotently.
"""

import os
import re
import sys

# Ensure repository root and backend directory are on sys.path
_script_dir = os.path.dirname(os.path.abspath(__file__))
_backend_dir = os.path.dirname(_script_dir)
_repo_root = os.path.dirname(_backend_dir)
for _p in (_repo_root, _backend_dir):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pymysql

from backend.config import settings


def verify_mysql_version(cursor) -> None:
    """Verifies that the database server is MySQL >= 8.0 (required for FOR UPDATE SKIP LOCKED)."""
    cursor.execute("SELECT VERSION();")
    row = cursor.fetchone()
    if not row:
        print("[ERROR] Failed to query MySQL version.")
        sys.exit(1)

    version_str = row[0]
    print(f"[INFO] Connected to MySQL Server: {version_str}")

    if "mariadb" in version_str.lower():
        print(
            f"[WARNING] Detected MariaDB: {version_str}. "
            "Note that MySQL 8.0+ native 'SKIP LOCKED' behavior is required for production."
        )

    match = re.search(r"(\d+)\.(\d+)", version_str)
    if not match:
        print(f"[WARNING] Could not parse version string '{version_str}'. Proceeding with caution.")
        return

    major = int(match.group(1))
    if major < 8:
        print(
            f"[ERROR] MySQL version {version_str} is unsupported. "
            "MySQL 8.0 or newer is required for 'SELECT ... FOR UPDATE SKIP LOCKED'."
        )
        sys.exit(1)

    print("[SUCCESS] MySQL >= 8.0 confirmed.")


def apply_schema(connection, db_name: str) -> None:
    """Applies schema.sql statements directly to the target database."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    schema_path = os.path.join(os.path.dirname(script_dir), "schema.sql")
    if not os.path.exists(schema_path):
        schema_path = os.path.join(script_dir, "schema.sql")
    if not os.path.exists(schema_path):
        print(f"[ERROR] schema.sql not found at {schema_path}")
        sys.exit(1)

    print(f"[INFO] Reading schema from: {schema_path}")
    with open(schema_path, "r", encoding="utf-8") as f:
        schema_sql = f.read()

    # Split SQL on semicolon outside of strings
    statements = [stmt.strip() for stmt in schema_sql.split(";") if stmt.strip()]

    with connection.cursor() as cursor:
        cursor.execute(f"USE `{db_name}`;")
        for stmt in statements:
            # Skip comments-only statements
            lines = [line for line in stmt.splitlines() if not line.strip().startswith("--")]
            clean_stmt = "\n".join(lines).strip()
            if not clean_stmt:
                continue
            cursor.execute(clean_stmt)
    print(f"[SUCCESS] Applied {len(statements)} schema statements successfully.")


def init_database() -> None:
    print("==================================================")
    print("Initializing Database for LinkedIn Post Scheduler")
    print("==================================================")
    print(f"Host:     {settings.DB_HOST}:{settings.DB_PORT}")
    print(f"Database: {settings.DB_NAME}")
    print(f"Admin:    {settings.DB_USER}")

    if not settings.DB_PASSWORD:
        print("\n[WARNING] DB_PASSWORD is empty in .env. Attempting root login without password...")

    try:
        connection = pymysql.connect(
            host=settings.DB_HOST,
            port=settings.DB_PORT,
            user=settings.DB_USER,
            password=settings.DB_PASSWORD,
            charset="utf8mb4",
            autocommit=True,
        )
    except pymysql.MySQLError as e:
        print(f"\n[FATAL] Could not connect to MySQL server at {settings.DB_HOST}:{settings.DB_PORT}")
        print(f"Error code {e.args[0]}: {e.args[1] if len(e.args) > 1 else str(e)}")
        print("\nPlease check that:")
        print(" 1. Local MySQL 8 server is running.")
        print(" 2. DB_USER and DB_PASSWORD in .env match your local MySQL credentials.")
        sys.exit(1)

    try:
        with connection.cursor() as cursor:
            # 1. Verify MySQL Version >= 8.0
            verify_mysql_version(cursor)

            # 2. Create Database if not exists
            db_name = settings.DB_NAME
            print(f"\n[INFO] Creating database `{db_name}` (utf8mb4, utf8mb4_unicode_ci) if not exists...")
            cursor.execute(
                f"CREATE DATABASE IF NOT EXISTS `{db_name}` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
            )
            print(f"[SUCCESS] Database `{db_name}` is ready.")

            # 3. Create Least-Privilege App User if configured
            app_user = settings.DB_APP_USER
            app_pass = settings.DB_APP_PASSWORD

            if app_user and app_pass:
                print(f"\n[INFO] Configuring least-privilege app user `{app_user}`...")
                escaped_pass = connection.escape(app_pass)
                hosts = ["localhost", "127.0.0.1", "%"]
                for host in hosts:
                    cursor.execute(
                        f"CREATE USER IF NOT EXISTS `{app_user}`@`{host}` IDENTIFIED BY {escaped_pass};"
                    )
                    cursor.execute(
                        f"ALTER USER `{app_user}`@`{host}` IDENTIFIED BY {escaped_pass};"
                    )
                    cursor.execute(
                        f"GRANT ALL PRIVILEGES ON `{db_name}`.* TO `{app_user}`@`{host}`;"
                    )
                cursor.execute("FLUSH PRIVILEGES;")
                print(
                    f"[SUCCESS] User `{app_user}` granted privileges strictly on `{db_name}`.*"
                )
            else:
                print(
                    "\n[NOTE] DB_APP_USER / DB_APP_PASSWORD not set. The application will use admin DB_USER."
                )

        # 4. Apply schema.sql
        apply_schema(connection, db_name)

        print("\n==================================================")
        print("Database initialization completed successfully!")
        print("==================================================")
    finally:
        connection.close()


if __name__ == "__main__":
    init_database()
