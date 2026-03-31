from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any


class SchemaIntrospector:
    def __init__(self, db_path: str | Path, table_name: str = "gaming_mental_health") -> None:
        self.db_path = Path(db_path)
        self.table_name = table_name
        self._schema_text: str | None = None
        self._columns: list[dict[str, Any]] = []

    @property
    def columns(self) -> list[dict[str, Any]]:
        if not self._columns:
            self._introspect()
        return self._columns

    @property
    def column_names(self) -> set[str]:
        return {col["name"].lower() for col in self.columns}

    def get_schema_text(self) -> str:
        if self._schema_text is None:
            self._introspect()
        return self._schema_text  # type: ignore[return-value]

    def _introspect(self) -> None:
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.cursor()
            cursor.execute(f'PRAGMA table_info("{self.table_name}")')
            raw_columns = cursor.fetchall()

            self._columns = []
            lines = [f"Table: {self.table_name}", "Columns:"]

            for col in raw_columns:
                _cid, name, dtype, _notnull, _dflt, _pk = col
                col_info: dict[str, Any] = {"name": name, "type": dtype}
                self._columns.append(col_info)

                if dtype == "TEXT":
                    cursor.execute(
                        f'SELECT DISTINCT "{name}" FROM "{self.table_name}" WHERE "{name}" IS NOT NULL LIMIT 6'
                    )
                    distinct = [row[0] for row in cursor.fetchall()]
                    if distinct:
                        col_info["samples"] = distinct
                        lines.append(f"  - {name} ({dtype}) values: {distinct}")
                    else:
                        lines.append(f"  - {name} ({dtype})")
                else:
                    lines.append(f"  - {name} ({dtype})")

            self._schema_text = "\n".join(lines)
        finally:
            conn.close()
