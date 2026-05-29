# db2api-service

This project discovers database tables through SQLAlchemy reflection and exposes generic REST endpoints for them.

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
