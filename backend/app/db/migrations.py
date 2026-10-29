"""轻量数据库迁移。

SQLAlchemy 的 ``create_all`` 只会建缺失的表，**不会给已存在的表加列**。
所以每次给表加字段，都要在 ``_MIGRATIONS`` 里登记一条。

写法：

```python
_MIGRATIONS = [
    ("message_citations", "source_group", "VARCHAR(16)"),
]
```

``init_db`` 启动时会检查列是否存在，缺就 ALTER TABLE ADD COLUMN。
"""

from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

logger = logging.getLogger(__name__)

# (表名, 列名, 列定义)
_COLUMN_MIGRATIONS: list[tuple[str, str, str]] = [
    ("message_citations", "source_group", "VARCHAR(16)"),
    ("message_citations", "origin", "VARCHAR(64)"),
    ("message_citations", "judge_cites_json", "TEXT"),
]

# (表名, 索引名, 建索引 SQL)
_INDEX_MIGRATIONS: list[tuple[str, str, str]] = [
    (
        "message_citations",
        "uq_cit_msg_n",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_cit_msg_n "
        "ON message_citations (message_id, n)",
    ),
]

# 唯一索引前要清的重复：(表, 保留规则 SQL)
_DEDUP_BEFORE_INDEX: list[tuple[str, str]] = [
    (
        "message_citations",
        "DELETE FROM message_citations WHERE id NOT IN "
        "(SELECT MIN(id) FROM message_citations GROUP BY message_id, n)",
    ),
]


async def _table_columns(conn: AsyncConnection, table: str) -> set[str]:
    res = await conn.execute(
        text("SELECT name FROM pragma_table_info(:t)"), {"t": table}
    )
    return {r[0] for r in res.fetchall()}


async def run_migrations(conn: AsyncConnection) -> None:
    """执行所有待办的迁移。幂等，重复调用安全。"""
    for table, column, ddl in _COLUMN_MIGRATIONS:
        cols = await _table_columns(conn, table)
        if column in cols:
            continue
        try:
            await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
            logger.info("迁移：%s 加列 %s", table, column)
        except Exception as exc:  # noqa: BLE001
            logger.warning("迁移失败 %s.%s: %s", table, column, exc)

    for table, ddl in _DEDUP_BEFORE_INDEX:
        try:
            await conn.execute(text(ddl))
        except Exception as exc:  # noqa: BLE001
            logger.warning("去重失败 %s: %s", table, exc)

    for table, name, ddl in _INDEX_MIGRATIONS:
        try:
            await conn.execute(text(ddl))
        except Exception as exc:  # noqa: BLE001
            logger.warning("建索引失败 %s.%s: %s", table, name, exc)
