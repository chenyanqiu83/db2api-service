# db2api-service

这个项目通过 SQLAlchemy 的反射能力发现数据库表，并为这些表提供通用的 REST 接口。

## 支持的数据库

该服务面向与 SQLAlchemy 兼容的关系型数据库，并不是所有数据库产品的通用适配层。

- 本项目明确支持：SQLite、PostgreSQL、MySQL、SQL Server。
- 其他与 SQLAlchemy 兼容的关系型数据库，如果支持反射并具备标准 CRUD 语义，通常也有机会正常工作。
- 默认不支持：MongoDB、Redis、Cassandra、Elasticsearch 以及其他非关系型存储。

实际是否可用，主要取决于以下三点：

- SQLAlchemy 是否为目标数据库提供可用的 dialect 和 DBAPI 驱动。
- 表反射是否能正确返回列信息和主键信息。
- 标准的插入、查询、更新、删除语义是否适用于目标表。

## 功能说明

- 扫描已配置数据库，并为每张表暴露对应的 CRUD 接口。
- 根据最新反射得到的表结构校验请求体字段名。
- 按可配置时间间隔自动刷新表元数据。
- 提供显式的 schema 刷新接口，便于在 DDL 变更后立即重新加载结构。

## 工作方式

该服务不会为每张表生成单独的 Python 文件，而是在内存中维护一个反射得到的表注册表，并通过通用处理逻辑转发所有 CRUD 请求：

- `GET /metadata` 返回当前反射到的表和列信息。
- `POST /admin/refresh` 强制执行一次完整元数据刷新。
- `GET /api/{table}` 列出表中的多条记录。
- `GET /api/{table}/{pk}` 获取单条记录。
- `POST /api/{table}` 创建一条记录。
- `PATCH /api/{table}/{pk}` 更新一条记录。
- `DELETE /api/{table}/{pk}` 删除一条记录。

### 列表查询参数

`GET /api/{table}` 支持通过查询字符串执行过滤、排序和分页。

- 等值过滤：除 `limit`、`offset`、`order_by`、`desc`、`refresh` 之外，其他与列名同名的查询参数都会被视为过滤条件。例如 `GET /api/users?name=Alice&status=active`。
- 排序：使用 `order_by=列名` 指定排序字段，使用 `desc=true` 切换为倒序。当前只支持单字段排序；如果未指定 `order_by` 且目标表存在主键，则默认按主键升序排序。
- 分页：使用 `limit` 控制返回条数，默认值为 `50`；使用 `offset` 控制跳过的记录数，默认值为 `0`。实际生效的 `limit` 不会超过 `MAX_PAGE_SIZE`，默认上限为 `200`。
- 强制刷新：使用 `refresh=true` 可以在执行查询前先刷新一次元数据。
- 错误处理：如果过滤字段或 `order_by` 指向不存在的列，接口会返回 `400`。

示例请求：

```http
GET /api/users?name=Alice&order_by=created_at&desc=true&limit=20&offset=40
```

返回结果包含以下字段：

- `table`：当前查询的表名。
- `total`：满足过滤条件的总记录数。
- `limit`：本次实际生效的分页大小。
- `offset`：本次分页偏移量。
- `filters`：实际应用的过滤条件。
- `items`：当前页的数据列表。

如果是复合主键，请按照主键顺序使用逗号分隔的标识，例如 `GET /api/order_items/1001,7`。

## 快速开始

1. 创建并激活虚拟环境。
2. 安装项目：

   ```powershell
   pip install -e .[test]
   ```

   如果目标数据库需要额外驱动，可按需安装对应扩展依赖：

   ```powershell
   pip install -e .[postgresql]
   pip install -e .[mysql]
   pip install -e .[sqlserver]
   ```

3. 将 `.env.example` 复制为 `.env`，并设置 `DATABASE_URL`。
4. 启动服务：

   ```powershell
   uvicorn db2api_service.main:app --reload
   ```

5. 打开 `http://127.0.0.1:8000/docs` 查看 Swagger UI。

## 数据库 URL 示例

- SQLite: `sqlite:///./demo.db`
- PostgreSQL: `postgresql+psycopg://user:password@host:5432/dbname`
- MySQL: `mysql+pymysql://user:password@host:3306/dbname`
- SQL Server: `mssql+pyodbc://user:password@host:1433/dbname?driver=ODBC+Driver+18+for+SQL+Server&TrustServerCertificate=yes`

如有需要，请安装匹配的可选驱动依赖：

```powershell
pip install -e .[postgres]
pip install -e .[mysql]
pip install -e .[sqlserver]
```

对于 SQL Server，还需要在宿主机上安装 Microsoft 的 ODBC 驱动。

## 说明

- 没有主键的表仍然支持集合读取和插入，但按行读取、更新和删除操作仍然依赖主键。
- schema 自动刷新行为由 `SCHEMA_REFRESH_INTERVAL_SECONDS` 控制。
- `GET /api/{table}` 支持简单等值过滤、单字段排序和分页，详细参数见上文“列表查询参数”。
- 服务通过 `SCHEMA_NAME` 一次反射一个指定 schema，这与 PostgreSQL、SQL Server、Oracle 一类的 schema 布局比较契合。
- `GET /health` 和 `GET /metadata` 会返回当前 SQLAlchemy dialect 和 driver，便于确认当前实际连接的后端类型。
