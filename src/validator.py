from __future__ import annotations

import re
import time

import sqlparse

from src.types import SQLValidationOutput

ALLOWED_TABLE = "gaming_mental_health"
MAX_RESULT_ROWS = 100

FORBIDDEN_KEYWORDS = re.compile(
    r"\b(DELETE|DROP|INSERT|UPDATE|ALTER|CREATE|TRUNCATE|ATTACH|DETACH|REINDEX|VACUUM)\b",
    re.IGNORECASE,
)

INJECTION_PATTERNS = [
    re.compile(
        r"ignore\s+(previous|above|all|prior)\s+(instructions?|prompts?|rules?|context)",
        re.IGNORECASE,
    ),
    re.compile(r"(pretend|act)\s+(as|like|you\s+are)\s+", re.IGNORECASE),
    re.compile(r"system\s*:?\s*(prompt|message|instruction)", re.IGNORECASE),
    re.compile(r"\bdo\s+not\s+follow\s+(any|the|your)\b", re.IGNORECASE),
    re.compile(r"\bjailbreak\b", re.IGNORECASE),
    re.compile(
        r"override\s+(your|all|any)\s+(rules?|instructions?|constraints?)",
        re.IGNORECASE,
    ),
]

_SQL_BUILTINS = frozenset(
    {
        "select",
        "from",
        "where",
        "and",
        "or",
        "not",
        "in",
        "between",
        "like",
        "is",
        "null",
        "as",
        "on",
        "join",
        "left",
        "right",
        "inner",
        "outer",
        "cross",
        "group",
        "by",
        "order",
        "asc",
        "desc",
        "limit",
        "offset",
        "having",
        "distinct",
        "case",
        "when",
        "then",
        "else",
        "end",
        "count",
        "sum",
        "avg",
        "min",
        "max",
        "cast",
        "coalesce",
        "ifnull",
        "round",
        "upper",
        "lower",
        "length",
        "substr",
        "trim",
        "replace",
        "abs",
        "total",
        "typeof",
        ALLOWED_TABLE,
    }
)


class SQLValidationError(Exception):
    pass


def _aliases_after_as_keyword(flat: list) -> set[str]:
    """Identifiers following AS (flattened tokens); allows ORDER BY / HAVING on aliases."""
    out: set[str] = set()
    for i, tok in enumerate(flat):
        if str(tok).strip().upper() != "AS":
            continue
        for k in range(i + 1, len(flat)):
            nt = flat[k]
            if nt.is_whitespace:
                continue
            if nt.ttype in (sqlparse.tokens.Name, sqlparse.tokens.Literal.String.Symbol):
                out.add(nt.value.strip('"').strip("'").lower())
            break
    return out


def _collect_select_aliases_from_statement(stmt: object) -> set[str]:
    """Names introduced by the SELECT list (AS alias / implicit name), lowercased."""
    idx_select: int | None = None
    idx_from: int | None = None
    for i, tok in enumerate(stmt.tokens):
        raw = str(tok).strip().upper()
        if idx_select is None and raw.startswith("SELECT"):
            idx_select = i
            continue
        if idx_select is not None and raw == "FROM":
            idx_from = i
            break

    if idx_select is None or idx_from is None or idx_from <= idx_select:
        return set()

    aliases: set[str] = set()
    for tok in stmt.tokens[idx_select + 1 : idx_from]:
        _aliases_from_select_token(tok, aliases)
    return aliases


def _aliases_from_select_token(token: object, aliases: set[str]) -> None:
    from sqlparse.sql import Identifier, IdentifierList

    if isinstance(token, IdentifierList):
        for ident in token.get_identifiers():
            _maybe_add_alias(ident, aliases)
    elif isinstance(token, Identifier):
        _maybe_add_alias(token, aliases)
    elif hasattr(token, "tokens"):
        for t in token.tokens:
            _aliases_from_select_token(t, aliases)


def _maybe_add_alias(ident: object, aliases: set[str]) -> None:
    from sqlparse.sql import Identifier

    if not isinstance(ident, Identifier):
        return
    alias = ident.get_alias()
    if alias:
        aliases.add(alias.strip('"').strip("'").lower())


class SQLValidator:
    @classmethod
    def validate(cls, sql: str | None, allowed_columns: set[str] | None = None) -> SQLValidationOutput:
        start = time.perf_counter()

        if sql is None:
            return cls._fail("No SQL provided", start)

        sql_stripped = sql.strip().rstrip(";").strip()
        if not sql_stripped:
            return cls._fail("Empty SQL statement", start)

        if FORBIDDEN_KEYWORDS.search(sql_stripped):
            return cls._fail("Only SELECT queries are allowed. Detected forbidden keyword.", start)

        if not sql_stripped.upper().lstrip().startswith("SELECT"):
            return cls._fail("Only SELECT queries are allowed.", start)

        try:
            parsed = sqlparse.parse(sql_stripped)
            if not parsed or not parsed[0].tokens:
                return cls._fail("SQL syntax could not be parsed.", start)
            if len(parsed) > 1:
                return cls._fail("Multi-statement SQL is not allowed.", start)
        except Exception as exc:
            return cls._fail(f"SQL parse error: {exc}", start)

        if allowed_columns:
            stmt = parsed[0]
            flat = list(stmt.flatten())
            select_aliases = _collect_select_aliases_from_statement(stmt) | _aliases_after_as_keyword(flat)
            identifiers = {
                tok.value.strip("\"'").lower()
                for tok in flat
                if tok.ttype in (sqlparse.tokens.Name, sqlparse.tokens.Literal.String.Symbol)
            }
            unknown = identifiers - allowed_columns - _SQL_BUILTINS - select_aliases
            if unknown:
                return cls._fail(f"Unknown column(s): {', '.join(sorted(unknown))}", start)

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

    @staticmethod
    def _fail(error: str, start: float) -> SQLValidationOutput:
        return SQLValidationOutput(
            is_valid=False,
            validated_sql=None,
            error=error,
            timing_ms=(time.perf_counter() - start) * 1000,
        )
