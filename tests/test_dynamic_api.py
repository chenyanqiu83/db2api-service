from __future__ import annotations

import sqlite3

from fastapi.testclient import TestClient

from db2api_service.config import Settings
from db2api_service.main import create_app
from db2api_service.schema_registry import SchemaRegistry


def test_settings_preserve_non_sqlite_urls() -> None:
    url = (
        "mssql+pyodbc://sa:password@dbhost:1433/appdb"
        "?driver=ODBC+Driver+18+for+SQL+Server&TrustServerCertificate=yes"
    )

    settings = Settings(database_url=url, schema_name="dbo")

    assert settings.database_url == url
    assert settings.schema_name == "dbo"


def test_registry_reflects_schema_changes(tmp_path) -> None:
    db_path = tmp_path / "dynamic.db"

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "create table users (id integer primary key autoincrement, name text not null)"
        )
        connection.commit()

    registry = SchemaRegistry(
        Settings(
            database_url=f"sqlite:///{db_path}",
            schema_refresh_interval_seconds=60,
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


def test_dynamic_crud_and_manual_refresh(tmp_path) -> None:
    db_path = tmp_path / "service.db"

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "create table users (id integer primary key autoincrement, name text not null)"
        )
        connection.commit()

    app = create_app(
        Settings(
            database_url=f"sqlite:///{db_path}",
            schema_refresh_interval_seconds=3600,
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

        refreshed = client.post("/admin/refresh")
        assert refreshed.status_code == 200
        assert "email" in refreshed.json()["tables"]["users"]["columns"]

        created_with_new_column = client.post(
            "/api/users",
            json={"name": "Bob", "email": "bob@example.com"},
        )
        assert created_with_new_column.status_code == 201
        assert created_with_new_column.json()["item"]["email"] == "bob@example.com"

        deleted = client.delete(f"/api/users/{user_id}")
        assert deleted.status_code == 200
        assert deleted.json()["deleted"] is True

        missing = client.get(f"/api/users/{user_id}")
        assert missing.status_code == 404