from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

from fastapi import Body, FastAPI, HTTPException, Query, Request, status
from fastapi.encoders import jsonable_encoder
from sqlalchemy import and_, delete, func, insert, select, update
from sqlalchemy.engine import Connection
from sqlalchemy.exc import SQLAlchemyError
import uvicorn

from db2api_service.config import Settings, get_settings
from db2api_service.schema_registry import (
    ColumnDescriptor,
    SchemaRegistry,
    TableDescriptor,
)


RESERVED_QUERY_PARAMS = {"limit", "offset", "order_by", "desc", "refresh"}


def _coerce_scalar(value: Any, descriptor: ColumnDescriptor) -> Any:
    if value is None or descriptor.python_type is None or isinstance(value, descriptor.python_type):
        return value

    python_type = descriptor.python_type
    if python_type is bool:
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"true", "1", "yes", "y", "on"}:
                return True
            if lowered in {"false", "0", "no", "n", "off"}:
                return False
        raise HTTPException(status_code=400, detail=f"Invalid boolean value for column '{descriptor.name}'.")
    if python_type is int:
        return int(value)
    if python_type is float:
        return float(value)
    if python_type is Decimal:
        return Decimal(str(value))
    if python_type is datetime:
        return datetime.fromisoformat(str(value))
    if python_type is date:
        return date.fromisoformat(str(value))
    if python_type is time:
        return time.fromisoformat(str(value))
    return python_type(value)


def _get_registry(request: Request) -> SchemaRegistry:
    return request.app.state.schema_registry


def _get_settings(request: Request) -> Settings:
    return request.app.state.settings


def _resolve_table(request: Request, table_name: str, refresh: bool = False) -> TableDescriptor:
    registry = _get_registry(request)
    table = registry.get_table(table_name, force_refresh=refresh)
    if table is None:
        raise HTTPException(status_code=404, detail=f"Table '{table_name}' was not found in the reflected schema.")
    return table


def _sanitize_payload(
    table: TableDescriptor,
    payload: dict[str, Any],
    *,
    for_update: bool,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Request body must be a JSON object.")

    unknown_fields = sorted(set(payload) - set(table.columns))
    if unknown_fields:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown columns for table '{table.name}': {', '.join(unknown_fields)}.",
        )

    allowed_columns = set(table.updatable_columns if for_update else table.writable_columns)
    disallowed_fields = sorted(set(payload) - allowed_columns)
    if disallowed_fields:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Columns are not writable for table '{table.name}': {', '.join(disallowed_fields)}."
            ),
        )

    if for_update and not payload:
        raise HTTPException(status_code=400, detail="Update payload cannot be empty.")

    if not for_update:
        missing_fields = [
            column_name for column_name in table.required_on_create if column_name not in payload
        ]
        if missing_fields:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Missing required columns for table '{table.name}': {', '.join(missing_fields)}."
                ),
            )

    return {
        column_name: _coerce_scalar(value, table.columns[column_name])
        for column_name, value in payload.items()
    }


def _build_where_clause(table: TableDescriptor, filters: dict[str, Any]) -> list[Any]:
    return [
        table.table.c[column_name].is_(None) if value is None else table.table.c[column_name] == value
        for column_name, value in filters.items()
    ]


def _parse_identity(table: TableDescriptor, identity: str) -> dict[str, Any]:
    if not table.supports_identity:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Table '{table.name}' has no primary key. Row-level read, update, and delete are unavailable."
            ),
        )

    raw_values = [value.strip() for value in identity.split(",") if value.strip()]
    if len(raw_values) != len(table.primary_keys):
        expected = ", ".join(table.primary_keys)
        raise HTTPException(
            status_code=400,
            detail=(
                f"Identity for table '{table.name}' must provide {len(table.primary_keys)} value(s) in primary key order: {expected}."
            ),
        )

    return {
        column_name: _coerce_scalar(raw_value, table.columns[column_name])
        for column_name, raw_value in zip(table.primary_keys, raw_values)
    }


def _parse_filters(table: TableDescriptor, request: Request) -> dict[str, Any]:
    filters: dict[str, Any] = {}
    for column_name, raw_value in request.query_params.items():
        if column_name in RESERVED_QUERY_PARAMS:
            continue
        if column_name not in table.columns:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown filter column for table '{table.name}': {column_name}.",
            )
        filters[column_name] = _coerce_scalar(raw_value, table.columns[column_name])
    return filters


def _fetch_one(connection: Connection, table: TableDescriptor, filters: dict[str, Any]) -> dict[str, Any] | None:
    stmt = select(table.table).where(and_(*_build_where_clause(table, filters)))
    row = connection.execute(stmt).mappings().first()
    if row is None:
        return None
    return jsonable_encoder(dict(row))


def _handle_db_error(exc: SQLAlchemyError) -> HTTPException:
    return HTTPException(status_code=400, detail=f"Database operation failed: {exc}")


def _snapshot_response(request: Request, *, refresh: bool) -> dict[str, Any]:
    registry = _get_registry(request)
    snapshot = registry.refresh(force=True) if refresh else registry.refresh_if_stale()
    summary = snapshot.to_summary()
    summary["database"] = registry.describe_database().to_summary()
    return summary


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or get_settings()
    registry = SchemaRegistry(resolved_settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        registry.refresh(force=True)
        yield
        registry.dispose()

    app = FastAPI(
        title=resolved_settings.app_name,
        version="0.1.0",
        lifespan=lifespan,
        description=(
            "Reflect database tables and expose generic CRUD REST endpoints that adapt to schema changes."
        ),
    )
    app.state.settings = resolved_settings
    app.state.schema_registry = registry

    @app.get("/health")
    def health(request: Request) -> dict[str, Any]:
        registry = _get_registry(request)
        snapshot = registry.refresh_if_stale()
        return {
            "status": "ok",
            "table_count": len(snapshot.tables),
            "schema_digest": snapshot.digest,
            "refreshed_at": snapshot.refreshed_at.isoformat(),
            "database": registry.describe_database().to_summary(),
        }

    @app.get("/metadata")
    def metadata(
        request: Request,
        refresh: bool = Query(default=False, description="Force a metadata refresh before returning."),
    ) -> dict[str, Any]:
        return _snapshot_response(request, refresh=refresh)

    @app.post("/admin/refresh")
    def refresh_metadata(request: Request) -> dict[str, Any]:
        return _snapshot_response(request, refresh=True)

    @app.get("/api/{table_name}")
    def list_rows(
        table_name: str,
        request: Request,
        limit: int = Query(default=50, ge=1, description="Maximum number of rows to return."),
        offset: int = Query(default=0, ge=0, description="Number of rows to skip."),
        order_by: str | None = Query(default=None, description="Optional column name to sort by."),
        desc: bool = Query(default=False, description="Sort descending when true."),
        refresh: bool = Query(default=False, description="Force a metadata refresh before serving the request."),
    ) -> dict[str, Any]:
        table = _resolve_table(request, table_name, refresh=refresh)
        filters = _parse_filters(table, request)
        settings = _get_settings(request)
        page_size = min(limit, settings.max_page_size)

        count_stmt = select(func.count()).select_from(table.table)
        stmt = select(table.table)
        clauses = _build_where_clause(table, filters)
        if clauses:
            count_stmt = count_stmt.where(and_(*clauses))
            stmt = stmt.where(and_(*clauses))

        if order_by is not None:
            if order_by not in table.columns:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unknown order_by column for table '{table.name}': {order_by}.",
                )
            order_column = table.table.c[order_by]
            stmt = stmt.order_by(order_column.desc() if desc else order_column.asc())
        elif table.primary_keys:
            stmt = stmt.order_by(*[table.table.c[column_name].asc() for column_name in table.primary_keys])

        stmt = stmt.limit(page_size).offset(offset)

        try:
            with _get_registry(request).engine.connect() as connection:
                total = connection.execute(count_stmt).scalar_one()
                rows = connection.execute(stmt).mappings().all()
        except SQLAlchemyError as exc:
            raise _handle_db_error(exc) from exc

        return {
            "table": table.name,
            "total": total,
            "limit": page_size,
            "offset": offset,
            "filters": filters,
            "items": [jsonable_encoder(dict(row)) for row in rows],
        }

    @app.get("/api/{table_name}/{identity}")
    def get_row(
        table_name: str,
        identity: str,
        request: Request,
        refresh: bool = Query(default=False, description="Force a metadata refresh before serving the request."),
    ) -> dict[str, Any]:
        table = _resolve_table(request, table_name, refresh=refresh)
        filters = _parse_identity(table, identity)

        try:
            with _get_registry(request).engine.connect() as connection:
                row = _fetch_one(connection, table, filters)
        except SQLAlchemyError as exc:
            raise _handle_db_error(exc) from exc

        if row is None:
            raise HTTPException(status_code=404, detail=f"Row was not found in table '{table.name}'.")

        return {
            "table": table.name,
            "primary_key": filters,
            "item": row,
        }

    @app.post("/api/{table_name}", status_code=status.HTTP_201_CREATED)
    def create_row(
        table_name: str,
        request: Request,
        payload: dict[str, Any] = Body(..., description="Column values for the new row."),
        refresh: bool = Query(default=False, description="Force a metadata refresh before serving the request."),
    ) -> dict[str, Any]:
        table = _resolve_table(request, table_name, refresh=refresh)
        clean_payload = _sanitize_payload(table, payload, for_update=False)

        try:
            with _get_registry(request).engine.begin() as connection:
                result = connection.execute(insert(table.table).values(**clean_payload))
                primary_key = {
                    column_name: clean_payload[column_name]
                    for column_name in table.primary_keys
                    if column_name in clean_payload
                }

                inserted_primary_key = list(result.inserted_primary_key or [])
                for index, column_name in enumerate(table.primary_keys):
                    if column_name not in primary_key and index < len(inserted_primary_key):
                        primary_key[column_name] = inserted_primary_key[index]

                row = None
                if len(primary_key) == len(table.primary_keys) and primary_key:
                    row = _fetch_one(connection, table, primary_key)
        except SQLAlchemyError as exc:
            raise _handle_db_error(exc) from exc

        return {
            "table": table.name,
            "primary_key": primary_key or None,
            "item": row or clean_payload,
        }

    @app.patch("/api/{table_name}/{identity}")
    def update_row(
        table_name: str,
        identity: str,
        request: Request,
        payload: dict[str, Any] = Body(..., description="Partial row update payload."),
        refresh: bool = Query(default=False, description="Force a metadata refresh before serving the request."),
    ) -> dict[str, Any]:
        table = _resolve_table(request, table_name, refresh=refresh)
        filters = _parse_identity(table, identity)
        clean_payload = _sanitize_payload(table, payload, for_update=True)

        try:
            with _get_registry(request).engine.begin() as connection:
                current = _fetch_one(connection, table, filters)
                if current is None:
                    raise HTTPException(status_code=404, detail=f"Row was not found in table '{table.name}'.")

                connection.execute(
                    update(table.table)
                    .where(and_(*_build_where_clause(table, filters)))
                    .values(**clean_payload)
                )
                updated_row = _fetch_one(connection, table, filters)
        except HTTPException:
            raise
        except SQLAlchemyError as exc:
            raise _handle_db_error(exc) from exc

        return {
            "table": table.name,
            "primary_key": filters,
            "item": updated_row,
        }

    @app.delete("/api/{table_name}/{identity}")
    def delete_row(
        table_name: str,
        identity: str,
        request: Request,
        refresh: bool = Query(default=False, description="Force a metadata refresh before serving the request."),
    ) -> dict[str, Any]:
        table = _resolve_table(request, table_name, refresh=refresh)
        filters = _parse_identity(table, identity)

        try:
            with _get_registry(request).engine.begin() as connection:
                current = _fetch_one(connection, table, filters)
                if current is None:
                    raise HTTPException(status_code=404, detail=f"Row was not found in table '{table.name}'.")

                connection.execute(
                    delete(table.table).where(and_(*_build_where_clause(table, filters)))
                )
        except HTTPException:
            raise
        except SQLAlchemyError as exc:
            raise _handle_db_error(exc) from exc

        return {
            "table": table.name,
            "primary_key": filters,
            "deleted": True,
            "item": current,
        }

    return app


app = create_app()


def run() -> None:
    uvicorn.run("db2api_service.main:app", host="127.0.0.1", port=8000, reload=False)


if __name__ == "__main__":
    run()
