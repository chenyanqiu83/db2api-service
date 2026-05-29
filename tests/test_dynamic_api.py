from __future__ import annotations

import sqlite3

from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError, ProgrammingError

from db2api_service.config import Settings
from db2api_service.main import _should_retry_after_db_error, create_app
from db2api_service.schema_registry import SchemaRegistry


class _FakePgError(Exception):
    def __init__(self, message: str, pgcode: str) -> None:
        super().__init__(message)
        self.pgcode = pgcode


def _test_settings(**overrides) -> Settings:
    defaults: dict[str, object] = {
        "_env_file": None,
        "schema_name": None,
        "include_tables": [],
        "exclude_tables": [],
    }
    defaults.update(overrides)
    return Settings(**defaults)


def test_settings_preserve_non_sqlite_urls() -> None:
    url = (
        "mssql+pyodbc://sa:password@dbhost:1433/appdb"
        "?driver=ODBC+Driver+18+for+SQL+Server&TrustServerCertificate=yes"
    )

    settings = _test_settings(database_url=url, schema_name="dbo")

    assert settings.database_url == url
    assert settings.schema_name == "dbo"


def test_settings_normalize_windows_sqlite_paths() -> None:
    settings = _test_settings(database_url=r"E:\data\demo.db")

    assert settings.database_url == "sqlite:///E:/data/demo.db"


def test_settings_ignore_empty_env_values(tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "DATABASE_URL=sqlite:///./demo.db",
                "SCHEMA_NAME=",
                "INCLUDE_TABLES=",
                "EXCLUDE_TABLES=",
            ]
        ),
        encoding="utf-8",
    )

    settings = Settings(_env_file=env_file)

    assert settings.schema_name is None
    assert settings.include_tables == []
    assert settings.exclude_tables == []


def test_create_app_does_not_require_optional_driver_on_construction() -> None:
    app = create_app(
        _test_settings(
            database_url="mysql+pymysql://user:password@dbhost:3306/appdb",
        )
    )

    assert app.title == "db2api-service"


def test_registry_reflects_schema_changes(tmp_path) -> None:
    db_path = tmp_path / "dynamic.db"

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "create table users (id integer primary key autoincrement, name text not null)"
        )
        connection.commit()

    registry = SchemaRegistry(
        _test_settings(
            database_url=f"sqlite:///{db_path}",
        )
    )

    first_snapshot = registry.refresh(force=True)
    assert "users" in first_snapshot.tables
    assert set(first_snapshot.tables["users"].columns) == {"id", "name"}

    with sqlite3.connect(db_path) as connection:
        connection.execute("alter table users add column email text")
        connection.commit()

    second_snapshot = registry.refresh(force=True)
    assert set(second_snapshot.tables["users"].columns) == {"id", "name", "email"}
    registry.dispose()


def test_dynamic_crud_auto_refreshes_after_schema_change(tmp_path) -> None:
    db_path = tmp_path / "service.db"

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "create table users (id integer primary key autoincrement, name text not null)"
        )
        connection.commit()

    app = create_app(
        _test_settings(
            database_url=f"sqlite:///{db_path}",
        )
    )

    with TestClient(app) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["table_count"] == 1
        assert health.json()["database"]["dialect"] == "sqlite"
        assert health.json()["database"]["drivername"] == "sqlite"

        metadata = client.get("/metadata")
        assert metadata.status_code == 200
        assert metadata.json()["database"]["dialect"] == "sqlite"
        assert metadata.json()["database"]["dbapi_driver"] == "pysqlite"

        created = client.post("/api/users", json={"name": "Alice"})
        assert created.status_code == 201
        user_id = created.json()["primary_key"]["id"]

        fetched = client.get(f"/api/users/{user_id}")
        assert fetched.status_code == 200
        assert fetched.json()["item"]["name"] == "Alice"

        updated = client.patch(f"/api/users/{user_id}", json={"name": "Alice Updated"})
        assert updated.status_code == 200
        assert updated.json()["item"]["name"] == "Alice Updated"

        listed = client.get("/api/users", params={"name": "Alice Updated"})
        assert listed.status_code == 200
        assert listed.json()["total"] == 1

        with sqlite3.connect(db_path) as connection:
            connection.execute("alter table users add column email text")
            connection.commit()

        created_with_new_column = client.post(
            "/api/users",
            json={"name": "Bob", "email": "bob@example.com"},
        )
        assert created_with_new_column.status_code == 201
        assert created_with_new_column.json()["item"]["email"] == "bob@example.com"

        listed_after_schema_change = client.get("/api/users", params={"email": "bob@example.com"})
        assert listed_after_schema_change.status_code == 200
        assert listed_after_schema_change.json()["total"] == 1

        metadata_after_schema_change = client.get("/metadata")
        assert metadata_after_schema_change.status_code == 200
        assert "email" in metadata_after_schema_change.json()["tables"]["users"]["columns"]

        deleted = client.delete(f"/api/users/{user_id}")
        assert deleted.status_code == 200
        assert deleted.json()["deleted"] is True

        missing = client.get(f"/api/users/{user_id}")
        assert missing.status_code == 404


def test_update_auto_refreshes_after_schema_change(tmp_path) -> None:
    db_path = tmp_path / "update-after-ddl.db"

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "create table users (id integer primary key autoincrement, name text not null)"
        )
        connection.commit()

    app = create_app(
        _test_settings(
            database_url=f"sqlite:///{db_path}",
        )
    )

    with TestClient(app) as client:
        created = client.post("/api/users", json={"name": "Alice"})
        assert created.status_code == 201
        user_id = created.json()["primary_key"]["id"]

        with sqlite3.connect(db_path) as connection:
            connection.execute("alter table users add column email text")
            connection.commit()

        updated = client.patch(
            f"/api/users/{user_id}",
            json={"email": "alice@example.com"},
        )
        assert updated.status_code == 200
        assert updated.json()["item"]["email"] == "alice@example.com"

        fetched = client.get(f"/api/users/{user_id}")
        assert fetched.status_code == 200
        assert fetched.json()["item"]["email"] == "alice@example.com"


def test_list_rows_auto_refreshes_when_order_by_uses_new_column(tmp_path) -> None:
    db_path = tmp_path / "order-by-after-ddl.db"

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "create table users (id integer primary key autoincrement, name text not null)"
        )
        connection.execute("insert into users (name) values ('Alice')")
        connection.execute("insert into users (name) values ('Bob')")
        connection.commit()

    app = create_app(
        _test_settings(
            database_url=f"sqlite:///{db_path}",
        )
    )

    with TestClient(app) as client:
        initial_list = client.get("/api/users")
        assert initial_list.status_code == 200
        assert initial_list.json()["total"] == 2

        with sqlite3.connect(db_path) as connection:
            connection.execute("alter table users add column created_at integer")
            connection.execute("update users set created_at = 2 where name = 'Alice'")
            connection.execute("update users set created_at = 1 where name = 'Bob'")
            connection.commit()

        ordered = client.get("/api/users", params={"order_by": "created_at"})
        assert ordered.status_code == 200
        assert ordered.json()["total"] == 2
        assert [item["name"] for item in ordered.json()["items"]] == ["Bob", "Alice"]


def test_schema_retry_detection_handles_dialect_specific_db_errors() -> None:
    sqlite_missing_column = OperationalError(
        statement=None,
        params=None,
        orig=Exception("no such column: email"),
    )
    postgres_missing_column = ProgrammingError(
        statement=None,
        params=None,
        orig=_FakePgError('column "email" does not exist', "42703"),
    )
    unrelated_error = ProgrammingError(
        statement=None,
        params=None,
        orig=Exception("duplicate key value violates unique constraint"),
    )

    assert _should_retry_after_db_error(sqlite_missing_column) is True
    assert _should_retry_after_db_error(postgres_missing_column) is True
    assert _should_retry_after_db_error(unrelated_error) is False