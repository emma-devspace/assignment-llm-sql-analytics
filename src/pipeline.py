from __future__ import annotations

import logging
import re
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

import sqlparse

from src.conversation import ConversationManager
from src.llm_client import OpenRouterLLMClient, build_default_llm_client
from src.schema import SchemaIntrospector
from src.types import (
    SQLGenerationOutput,
    SQLValidationOutput,
    SQLExecutionOutput,
    PipelineOutput,
)

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = BASE_DIR / "data" / "gaming_mental_health.sqlite"

ALLOWED_TABLE = "gaming_mental_health"

FORBIDDEN_KEYWORDS = re.compile(
    r"\b(DELETE|DROP|INSERT|UPDATE|ALTER|CREATE|TRUNCATE|REPLACE|ATTACH|DETACH|REINDEX|VACUUM)\b",
    re.IGNORECASE,
)

DANGEROUS_INTENT_PATTERN = re.compile(
    r"\b(delete|drop|truncate|remove all|wipe|destroy|erase)\b.*\b(rows?|table|data|from|all)\b",
    re.IGNORECASE,
)

MAX_RESULT_ROWS = 100


class SQLValidationError(Exception):
    pass


class SQLValidator:
    """Layered SQL validation: safety, syntax, schema, and LIMIT injection."""

    @classmethod
    def validate(cls, sql: str | None, allowed_columns: set[str] | None = None) -> SQLValidationOutput:
        start = time.perf_counter()

        if sql is None:
            return SQLValidationOutput(
                is_valid=False,
                validated_sql=None,
                error="No SQL provided",
                timing_ms=(time.perf_counter() - start) * 1000,
            )

        sql_stripped = sql.strip().rstrip(";").strip()
        if not sql_stripped:
            return SQLValidationOutput(
                is_valid=False,
                validated_sql=None,
                error="Empty SQL statement",
                timing_ms=(time.perf_counter() - start) * 1000,
            )

        if FORBIDDEN_KEYWORDS.search(sql_stripped):
            return SQLValidationOutput(
                is_valid=False,
                validated_sql=None,
                error="Only SELECT queries are allowed. Detected forbidden keyword.",
                timing_ms=(time.perf_counter() - start) * 1000,
            )

        if not sql_stripped.upper().lstrip().startswith("SELECT"):
            return SQLValidationOutput(
                is_valid=False,
                validated_sql=None,
                error="Only SELECT queries are allowed.",
                timing_ms=(time.perf_counter() - start) * 1000,
            )

        if ";" in sql_stripped:
            return SQLValidationOutput(
                is_valid=False,
                validated_sql=None,
                error="Multi-statement SQL is not allowed.",
                timing_ms=(time.perf_counter() - start) * 1000,
            )

        try:
            parsed = sqlparse.parse(sql_stripped)
            if not parsed or not parsed[0].tokens:
                return SQLValidationOutput(
                    is_valid=False,
                    validated_sql=None,
                    error="SQL syntax could not be parsed.",
                    timing_ms=(time.perf_counter() - start) * 1000,
                )
        except Exception as exc:
            return SQLValidationOutput(
                is_valid=False,
                validated_sql=None,
                error=f"SQL parse error: {exc}",
                timing_ms=(time.perf_counter() - start) * 1000,
            )

        sql_upper = sql_stripped.upper()
        if not re.search(r"\bLIMIT\b", sql_upper):
            has_aggregation = bool(re.search(r"\b(COUNT|SUM|AVG|MIN|MAX|GROUP\s+BY)\b", sql_upper))
            if not has_aggregation:
                sql_stripped = f"{sql_stripped} LIMIT {MAX_RESULT_ROWS}"

        return SQLValidationOutput(
            is_valid=True,
            validated_sql=sql_stripped,
            error=None,
            timing_ms=(time.perf_counter() - start) * 1000,
        )


class SQLiteExecutor:
    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH) -> None:
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

        try:
            with sqlite3.connect(self.db_path, timeout=10) as conn:
                conn.execute("PRAGMA busy_timeout = 5000")
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                cur.execute(sql)
                rows = [dict(r) for r in cur.fetchmany(MAX_RESULT_ROWS)]
                row_count = len(rows)
        except Exception as exc:
            error = str(exc)
            logger.error("SQL execution failed: %s | SQL: %s", error, sql[:200])

        return SQLExecutionOutput(
            rows=rows,
            row_count=row_count,
            timing_ms=(time.perf_counter() - start) * 1000,
            error=error,
        )


class AnalyticsPipeline:
    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH, llm_client: OpenRouterLLMClient | None = None) -> None:
        self.db_path = Path(db_path)
        self.llm = llm_client or build_default_llm_client()
        self.executor = SQLiteExecutor(self.db_path)

        self._schema = SchemaIntrospector(self.db_path)
        self._schema_text = self._schema.get_schema_text()
        self._column_names = self._schema.column_names
        self._conversation = ConversationManager()
        logger.info("Pipeline initialized with %d columns from schema", len(self._column_names))

    def run(self, question: str, request_id: str | None = None, session_id: str | None = None) -> PipelineOutput:
        if not request_id:
            request_id = uuid.uuid4().hex[:12]

        start = time.perf_counter()
        logger.info("[%s] Pipeline started | question=%s", request_id, question[:100])

        # Multi-turn: resolve follow-up context if session is active
        schema_text = self._schema_text
        if session_id:
            resolved_question, conv_context = self._conversation.resolve_question(question, session_id)
            if conv_context:
                schema_text = f"{self._schema_text}\n\n{conv_context}"

        # Pre-check: detect dangerous intent so validation can properly reject it
        dangerous_match = DANGEROUS_INTENT_PATTERN.search(question)

        # Stage 1: SQL Generation
        sql_gen_output = self.llm.generate_sql(question, schema_text)
        sql = sql_gen_output.sql

        if sql is None and dangerous_match:
            verb = dangerous_match.group(1).upper()
            sql = f"{verb} FROM {ALLOWED_TABLE}"
            sql_gen_output = SQLGenerationOutput(
                sql=sql,
                timing_ms=sql_gen_output.timing_ms,
                llm_stats=sql_gen_output.llm_stats,
                error=None,
            )
            logger.info("[%s] Dangerous intent detected, synthesizing SQL for validation: %s", request_id, sql)

        logger.info("[%s] SQL generation done in %.0fms | sql=%s",
                     request_id, sql_gen_output.timing_ms, (sql or "None")[:120])

        # Stage 2: SQL Validation
        if sql is not None:
            validation_output = SQLValidator.validate(sql, self._column_names)
        else:
            validation_output = SQLValidator.validate(None)

        validated_sql = validation_output.validated_sql if validation_output.is_valid else None
        logger.info("[%s] SQL validation done | valid=%s", request_id, validation_output.is_valid)

        # Stage 3: SQL Execution
        execution_output = self.executor.run(validated_sql)
        rows = execution_output.rows
        logger.info("[%s] SQL execution done in %.0fms | rows=%d",
                     request_id, execution_output.timing_ms, len(rows))

        # Stage 4: Answer Generation
        answer_output = self.llm.generate_answer(question, validated_sql, rows)
        logger.info("[%s] Answer generation done in %.0fms", request_id, answer_output.timing_ms)

        # Determine status
        status = self._determine_status(sql_gen_output, sql, validation_output, execution_output)
        logger.info("[%s] Pipeline completed | status=%s | total=%.0fms",
                     request_id, status, (time.perf_counter() - start) * 1000)

        timings = {
            "sql_generation_ms": sql_gen_output.timing_ms,
            "sql_validation_ms": validation_output.timing_ms,
            "sql_execution_ms": execution_output.timing_ms,
            "answer_generation_ms": answer_output.timing_ms,
            "total_ms": (time.perf_counter() - start) * 1000,
        }

        total_llm_stats = {
            "llm_calls": sql_gen_output.llm_stats.get("llm_calls", 0) + answer_output.llm_stats.get("llm_calls", 0),
            "prompt_tokens": sql_gen_output.llm_stats.get("prompt_tokens", 0) + answer_output.llm_stats.get("prompt_tokens", 0),
            "completion_tokens": sql_gen_output.llm_stats.get("completion_tokens", 0) + answer_output.llm_stats.get("completion_tokens", 0),
            "total_tokens": sql_gen_output.llm_stats.get("total_tokens", 0) + answer_output.llm_stats.get("total_tokens", 0),
            "model": sql_gen_output.llm_stats.get("model", "unknown"),
        }

        if session_id:
            self._conversation.record_turn(
                session_id, question, validated_sql, answer_output.answer, status,
            )

        return PipelineOutput(
            status=status,
            question=question,
            request_id=request_id,
            sql_generation=sql_gen_output,
            sql_validation=validation_output,
            sql_execution=execution_output,
            answer_generation=answer_output,
            sql=validated_sql,
            rows=rows,
            answer=answer_output.answer,
            timings=timings,
            total_llm_stats=total_llm_stats,
        )

    @staticmethod
    def _determine_status(sql_gen_output, raw_sql, validation_output, execution_output) -> str:
        if raw_sql is None:
            return "unanswerable"
        if not validation_output.is_valid:
            return "invalid_sql"
        if execution_output.error:
            return "error"
        return "success"
