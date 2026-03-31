"""Unit tests for SQL validation logic. No LLM calls needed."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from src.pipeline import SQLValidator


class TestSQLValidatorSafety(unittest.TestCase):
    """SELECT-only enforcement and dangerous SQL blocking."""

    def test_valid_select_passes(self):
        result = SQLValidator.validate("SELECT * FROM gaming_mental_health")
        self.assertTrue(result.is_valid)
        self.assertIsNotNone(result.validated_sql)
        self.assertIsNone(result.error)

    def test_select_with_where_passes(self):
        result = SQLValidator.validate("SELECT age, gender FROM gaming_mental_health WHERE age > 20")
        self.assertTrue(result.is_valid)

    def test_select_with_aggregation_passes(self):
        result = SQLValidator.validate(
            "SELECT gender, AVG(addiction_level) FROM gaming_mental_health GROUP BY gender"
        )
        self.assertTrue(result.is_valid)

    def test_delete_rejected(self):
        result = SQLValidator.validate("DELETE FROM gaming_mental_health")
        self.assertFalse(result.is_valid)
        self.assertIn("SELECT", result.error)

    def test_drop_rejected(self):
        result = SQLValidator.validate("DROP TABLE gaming_mental_health")
        self.assertFalse(result.is_valid)

    def test_insert_rejected(self):
        result = SQLValidator.validate("INSERT INTO gaming_mental_health VALUES (1, 2, 3)")
        self.assertFalse(result.is_valid)

    def test_update_rejected(self):
        result = SQLValidator.validate("UPDATE gaming_mental_health SET age = 99")
        self.assertFalse(result.is_valid)

    def test_alter_rejected(self):
        result = SQLValidator.validate("ALTER TABLE gaming_mental_health ADD COLUMN foo TEXT")
        self.assertFalse(result.is_valid)

    def test_truncate_rejected(self):
        result = SQLValidator.validate("TRUNCATE TABLE gaming_mental_health")
        self.assertFalse(result.is_valid)

    def test_create_rejected(self):
        result = SQLValidator.validate("CREATE TABLE evil (id INTEGER)")
        self.assertFalse(result.is_valid)


class TestSQLValidatorInjection(unittest.TestCase):
    """Multi-statement and injection prevention."""

    def test_semicolon_injection_rejected(self):
        result = SQLValidator.validate("SELECT 1; DROP TABLE gaming_mental_health")
        self.assertFalse(result.is_valid)
        self.assertIn("forbidden", result.error.lower())

    def test_trailing_semicolon_stripped(self):
        result = SQLValidator.validate("SELECT * FROM gaming_mental_health;")
        self.assertTrue(result.is_valid)
        self.assertNotIn(";", result.validated_sql)


class TestSQLValidatorLimitInjection(unittest.TestCase):
    """LIMIT clause injection when missing."""

    def test_limit_injected_for_non_aggregate(self):
        result = SQLValidator.validate("SELECT * FROM gaming_mental_health")
        self.assertTrue(result.is_valid)
        self.assertIn("LIMIT", result.validated_sql.upper())

    def test_limit_not_injected_for_aggregation(self):
        result = SQLValidator.validate(
            "SELECT gender, COUNT(*) FROM gaming_mental_health GROUP BY gender"
        )
        self.assertTrue(result.is_valid)
        self.assertNotIn("LIMIT", result.validated_sql.upper())

    def test_existing_limit_preserved(self):
        sql = "SELECT * FROM gaming_mental_health LIMIT 10"
        result = SQLValidator.validate(sql)
        self.assertTrue(result.is_valid)
        self.assertEqual(result.validated_sql.count("LIMIT"), 1)


class TestSQLValidatorEdgeCases(unittest.TestCase):
    """Edge cases and boundary conditions."""

    def test_none_input(self):
        result = SQLValidator.validate(None)
        self.assertFalse(result.is_valid)
        self.assertIsNone(result.validated_sql)

    def test_empty_string(self):
        result = SQLValidator.validate("")
        self.assertFalse(result.is_valid)

    def test_whitespace_only(self):
        result = SQLValidator.validate("   ")
        self.assertFalse(result.is_valid)

    def test_non_select_statement(self):
        result = SQLValidator.validate("EXPLAIN SELECT * FROM gaming_mental_health")
        self.assertFalse(result.is_valid)

    def test_timing_is_non_negative(self):
        result = SQLValidator.validate("SELECT 1")
        self.assertGreaterEqual(result.timing_ms, 0.0)


if __name__ == "__main__":
    unittest.main()
