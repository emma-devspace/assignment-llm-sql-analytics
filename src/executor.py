from __future__ import annotations

import logging
import sqlite3
import time
from pathlib import Path
from typing import Any

from src.types import SQLExecutionOutput

logger = logging.getLogger(__name__)

QUERY_TIMEOUT_SECONDS = 30
MAX_RESULT_ROWS = 100


class SQLiteExecutor:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)

    def run(self, sql: str | None) -> SQLExecutionOutput:
        start = time.perf_counter()
        error = None
        rows: list[dict[str, Any]] = []
        row_count = 0

        if sql is None:
            return SQLExecutionOutput(
                rows=[],
                row_count=0,
                timing_ms=(time.perf_counter() - start) * 1000,
                error=None,
            )

        query_start = time.monotonic()

        def _timeout_guard():
            return 1 if time.monotonic() - query_start > QUERY_TIMEOUT_SECONDS else 0

        try:
            with sqlite3.connect(self.db_path, timeout=10) as conn:
                conn.set_progress_handler(_timeout_guard, 10000)
                conn.execute("PRAGMA busy_timeout = 5000")
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                cur.execute(sql)
                rows = [dict(r) for r in cur.fetchmany(MAX_RESULT_ROWS)]
                row_count = len(rows)
        except sqlite3.OperationalError as exc:
            exc_str = str(exc)
            if "interrupted" in exc_str.lower():
                error = f"Query timed out (>{QUERY_TIMEOUT_SECONDS}s)"
            else:
                error = exc_str
            logger.error("SQL execution failed: %s | SQL: %s", error, sql[:200])
        except Exception as exc:
            error = str(exc)
            logger.error("SQL execution failed: %s | SQL: %s", error, sql[:200])

        return SQLExecutionOutput(
            rows=rows,
            row_count=row_count,
            timing_ms=(time.perf_counter() - start) * 1000,
            error=error,
        )
