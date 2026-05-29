# db2api-service

![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-2f6db5)
![FastAPI](https://img.shields.io/badge/framework-FastAPI-0b7a75)
![SQLAlchemy 2](https://img.shields.io/badge/ORM-SQLAlchemy%202-d71f00)
![Relational DBs](https://img.shields.io/badge/databases-SQLite%20%7C%20PostgreSQL%20%7C%20MySQL%20%7C%20SQL%20Server-5a3ec8)
![Automation Testing](https://img.shields.io/badge/use%20case-automation%20testing-3a7d44)

Dynamic REST API generation for relational databases with FastAPI and SQLAlchemy reflection.

Turn an existing database into a schema-aware CRUD service in minutes. db2api-service reflects tables at runtime, exposes generic REST endpoints, and handles schema drift with automatic metadata refresh and retry.

- Zero per-table route code for standard CRUD APIs.
- Works with SQLite, PostgreSQL, MySQL, and SQL Server.
- Built for internal tools, admin backends, rapid prototypes, and legacy database projects.
- Useful as a lightweight backend for automation testing, QA environments, and test data workflows.

Automation testing teams, QA platform engineers, and test infrastructure teams can use db2api-service as a dynamic test data API, database-backed integration testing backend, regression testing service, schema-aware CRUD layer, and disposable environment helper for CI pipelines and end-to-end test platforms.

## Quick example

Point the service at an existing relational database and immediately query reflected tables through HTTP:

```http
GET /api/users?status=active&order_by=created_at&desc=true&limit=10
```

```json
{
   "table": "users",
   "total": 42,
   "limit": 10,
   "offset": 0,
   "filters": {
      "status": "active"
   },
   "items": [
      {
         "id": 101,
         "name": "Alice",
         "status": "active",
         "created_at": "2026-05-30T09:15:00"
      }
   ]
}
```

## Supported databases

This service targets SQLAlchemy-compatible relational databases. It is not a universal adapter for every database product.

- Explicitly supported in this project: SQLite, PostgreSQL, MySQL, SQL Server.
- Other SQLAlchemy-compatible relational databases may also work if reflection and standard CRUD semantics are available.
- Not supported out of the box: MongoDB, Redis, Cassandra, Elasticsearch, and other non-relational stores.

In practice, support depends on three things:

- SQLAlchemy must provide a working dialect and DBAPI driver for the target database.
- Table reflection must correctly return columns and primary keys.
- Standard insert, select, update, and delete semantics must be valid for the target tables.

## Features

- Scans the configured database and exposes CRUD endpoints for each table.
- Validates request payload field names against the latest reflected schema.
- Loads table metadata once at startup and does not rely on timed refresh.
- Automatically refreshes metadata and retries once when schema drift causes a request to mismatch cached structure.
- Still provides an explicit schema refresh endpoint when manual reload is needed.
- Useful for automated API, integration, regression, and test-environment validation against existing databases.

## Good fit for

- Rapidly exposing internal relational databases as REST APIs.
- Building admin tools and internal tooling without writing per-table endpoints.
- Automated testing that needs predictable CRUD access to a real database schema.
- Regression testing for schema changes, especially when tables evolve during development.
- Legacy database projects where writing and maintaining custom API layers would be expensive.

## Why this project

- Use this when you already have a relational database and want an API now, not after building a custom CRUD layer for every table.
- Compared with hand-written FastAPI endpoints, it reduces repetitive route, validation, and metadata boilerplate for standard database operations.
- Compared with code generation, it keeps the API tied to the live reflected schema instead of generated files that can drift from the database.
- Compared with one-off test helpers, it gives automation and QA teams a reusable HTTP surface for seeding, querying, and validating real test data.
- Compared with admin-only tooling, it is designed to be scriptable from CI pipelines, integration suites, and end-to-end tests.

## How it works

The service does not generate separate Python files for each table. Instead, it keeps an in-memory reflected table registry and routes all CRUD requests through generic handlers:

- `GET /metadata` returns the currently reflected tables and columns.
- `POST /admin/refresh` forces a full metadata refresh.
- `GET /api/{table}` lists rows.
- `GET /api/{table}/{pk}` fetches a single row.
- `POST /api/{table}` creates a row.
- `PATCH /api/{table}/{pk}` updates a row.
- `DELETE /api/{table}/{pk}` deletes a row.

### List query parameters

`GET /api/{table}` supports filtering, sorting, and pagination through query string parameters.

- Equality filters: any query parameter matching a column name is treated as a filter, except `limit`, `offset`, `order_by`, `desc`, and `refresh`. Example: `GET /api/users?name=Alice&status=active`.
- Sorting: use `order_by=column_name` to choose the sort field and `desc=true` for descending order. Only single-column sorting is supported. If `order_by` is not provided and the table has a primary key, results default to primary-key ascending order.
- Pagination: use `limit` to control the number of rows returned, default `50`, and `offset` to skip rows, default `0`. The effective `limit` will not exceed `MAX_PAGE_SIZE`, which defaults to `200`.
- Forced refresh: use `refresh=true` to refresh metadata before executing the query.
- Error handling: if a filter field or `order_by` references a missing column, the API returns `400`.

Example request:

```http
GET /api/users?name=Alice&order_by=created_at&desc=true&limit=20&offset=40
```

The response includes these fields:

- `table`: the queried table name.
- `total`: total number of rows matching the filters.
- `limit`: effective page size used for the query.
- `offset`: pagination offset.
- `filters`: filters actually applied.
- `items`: the current page of rows.

For composite primary keys, use a comma-separated identity in primary key order, for example `GET /api/order_items/1001,7`.

## Quick start

1. Create and activate a virtual environment.
2. Install the project:

   ```powershell
   pip install -e .[test]
   ```

   Install the matching optional driver extras when needed:

   ```powershell
   pip install -e .[postgresql]
   pip install -e .[mysql]
   pip install -e .[sqlserver]
   ```

3. Copy `.env.example` to `.env` and set `DATABASE_URL`.
4. Start the service:

   ```powershell
   uvicorn db2api_service.main:app --reload
   ```

5. Open `http://127.0.0.1:8000/docs` for Swagger UI.

## Automated testing use cases

This project can also serve as a test helper service for teams that want to exercise real database-backed API flows without manually building a dedicated CRUD layer first.

- Use it in integration tests to seed, query, update, and delete relational test data through HTTP.
- Use it in end-to-end tests to validate UI or workflow behavior against a disposable database.
- Use it in regression suites to catch breaking schema changes when columns are added, removed, or renamed.
- Use it in QA environments to expose temporary database fixtures through a consistent API surface.

Because the service reflects the schema dynamically and can refresh metadata after drift-related failures, it is especially useful when test databases change frequently during development.

You can run the project test suite with:

```powershell
pytest
```

## Database URL examples

- SQLite: `sqlite:///./demo.db`
- PostgreSQL: `postgresql+psycopg://user:password@host:5432/dbname`
- MySQL: `mysql+pymysql://user:password@host:3306/dbname`
- SQL Server: `mssql+pyodbc://user:password@host:1433/dbname?driver=ODBC+Driver+18+for+SQL+Server&TrustServerCertificate=yes`

Install matching optional driver dependencies when needed:

```powershell
pip install -e .[postgres]
pip install -e .[mysql]
pip install -e .[sqlserver]
```

For SQL Server, you also need Microsoft's ODBC driver installed on the host machine.

## Notes

- Tables without a primary key still support collection reads and inserts, but row-level read, update, and delete operations require a primary key.
- The service does not use timed schema refresh. After startup, if a schema-drift-related error occurs, it automatically refreshes metadata and retries once.
- `GET /api/{table}` supports simple equality filters, single-column sorting, and pagination. See “List query parameters” above for details.
- The service reflects one configured schema at a time through `SCHEMA_NAME`, which fits PostgreSQL, SQL Server, and Oracle-style schema layouts well.
- `GET /health` and `GET /metadata` expose the active SQLAlchemy dialect and driver so you can confirm the connected backend.
