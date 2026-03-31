"""Unit tests for SQL extraction, token counting, and schema introspection. No LLM calls."""
from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from src.llm_client import OpenRouterLLMClient
from src.schema import SchemaIntrospector


class TestExtractSQL(unittest.TestCase):
    """Test _extract_sql static method with various LLM output formats."""

    def test_json_with_sql(self):
        text = '{"sql": "SELECT * FROM gaming_mental_health"}'
        result = OpenRouterLLMClient._extract_sql(text)
        self.assertEqual(result, "SELECT * FROM gaming_mental_health")

    def test_json_with_null_sql(self):
        text = '{"sql": null, "reason": "cannot answer"}'
        result = OpenRouterLLMClient._extract_sql(text)
        self.assertIsNone(result)

    def test_json_with_empty_sql(self):
        text = '{"sql": ""}'
        result = OpenRouterLLMClient._extract_sql(text)
        self.assertIsNone(result)

    def test_raw_select(self):
        text = "Here is the query:\nSELECT age FROM gaming_mental_health"
        result = OpenRouterLLMClient._extract_sql(text)
        self.assertTrue(result.startswith("SELECT"))
        self.assertIn("gaming_mental_health", result)

    def test_code_fence_json(self):
        text = '```json\n{"sql": "SELECT COUNT(*) FROM gaming_mental_health"}\n```'
        result = OpenRouterLLMClient._extract_sql(text)
        self.assertEqual(result, "SELECT COUNT(*) FROM gaming_mental_health")

    def test_code_fence_sql(self):
        text = '```sql\nSELECT * FROM gaming_mental_health\n```'
        result = OpenRouterLLMClient._extract_sql(text)
        self.assertEqual(result, "SELECT * FROM gaming_mental_health")

    def test_garbage_input(self):
        result = OpenRouterLLMClient._extract_sql("I don't know how to help")
        self.assertIsNone(result)

    def test_empty_input(self):
        result = OpenRouterLLMClient._extract_sql("")
        self.assertIsNone(result)

    def test_sql_with_trailing_semicolon(self):
        text = '{"sql": "SELECT * FROM gaming_mental_health;"}'
        result = OpenRouterLLMClient._extract_sql(text)
        self.assertIsNotNone(result)
        self.assertFalse(result.endswith(";"))

    def test_json_with_extra_fields(self):
        text = '{"sql": "SELECT 1", "explanation": "simple query"}'
        result = OpenRouterLLMClient._extract_sql(text)
        self.assertEqual(result, "SELECT 1")


class TestSchemaIntrospector(unittest.TestCase):
    """Test schema introspection with a temporary database."""

    @classmethod
    def setUpClass(cls):
        cls._tmpfile = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
        cls._db_path = Path(cls._tmpfile.name)
        cls._tmpfile.close()

        conn = sqlite3.connect(cls._db_path)
        conn.execute("""
            CREATE TABLE gaming_mental_health (
                age INTEGER,
                gender TEXT,
                addiction_level REAL,
                anxiety_score REAL
            )
        """)
        conn.execute("INSERT INTO gaming_mental_health VALUES (25, 'Male', 5.0, 3.0)")
        conn.execute("INSERT INTO gaming_mental_health VALUES (30, 'Female', 3.0, 7.0)")
        conn.execute("INSERT INTO gaming_mental_health VALUES (22, 'Non-binary', 8.0, 2.0)")
        conn.commit()
        conn.close()

    @classmethod
    def tearDownClass(cls):
        cls._db_path.unlink(missing_ok=True)

    def test_schema_text_contains_table_name(self):
        si = SchemaIntrospector(self._db_path)
        text = si.get_schema_text()
        self.assertIn("gaming_mental_health", text)

    def test_schema_text_contains_columns(self):
        si = SchemaIntrospector(self._db_path)
        text = si.get_schema_text()
        for col in ["age", "gender", "addiction_level", "anxiety_score"]:
            self.assertIn(col, text)

    def test_column_names_set(self):
        si = SchemaIntrospector(self._db_path)
        names = si.column_names
        self.assertEqual(names, {"age", "gender", "addiction_level", "anxiety_score"})

    def test_text_columns_have_sample_values(self):
        si = SchemaIntrospector(self._db_path)
        text = si.get_schema_text()
        self.assertIn("Male", text)
        self.assertIn("Female", text)

    def test_schema_cached_after_first_call(self):
        si = SchemaIntrospector(self._db_path)
        text1 = si.get_schema_text()
        text2 = si.get_schema_text()
        self.assertIs(text1, text2)


class TestTokenStats(unittest.TestCase):
    """Test pop_stats returns correct structure."""

    def test_pop_stats_returns_zeros_initially(self):
        client = OpenRouterLLMClient.__new__(OpenRouterLLMClient)
        client._stats = {"llm_calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        stats = client.pop_stats()
        self.assertEqual(stats["llm_calls"], 0)
        self.assertEqual(stats["prompt_tokens"], 0)
        self.assertEqual(stats["completion_tokens"], 0)
        self.assertEqual(stats["total_tokens"], 0)

    def test_pop_stats_resets_after_pop(self):
        client = OpenRouterLLMClient.__new__(OpenRouterLLMClient)
        client._stats = {"llm_calls": 3, "prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}
        first = client.pop_stats()
        second = client.pop_stats()
        self.assertEqual(first["llm_calls"], 3)
        self.assertEqual(first["total_tokens"], 150)
        self.assertEqual(second["llm_calls"], 0)
        self.assertEqual(second["total_tokens"], 0)


if __name__ == "__main__":
    unittest.main()
