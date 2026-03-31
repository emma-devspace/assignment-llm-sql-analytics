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
        text = "```sql\nSELECT * FROM gaming_mental_health\n```"
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


class TestQueryCache(unittest.TestCase):
    """Test LRU query cache behavior."""

    def _make_output(self, question: str, status: str = "success"):
        from src.types import (
            PipelineOutput,
            SQLGenerationOutput,
            SQLValidationOutput,
            SQLExecutionOutput,
            AnswerGenerationOutput,
        )

        return PipelineOutput(
            status=status,
            question=question,
            request_id="test123",
            sql_generation=SQLGenerationOutput(sql="SELECT 1", timing_ms=1.0, llm_stats={}, error=None),
            sql_validation=SQLValidationOutput(is_valid=True, validated_sql="SELECT 1", timing_ms=0.1),
            sql_execution=SQLExecutionOutput(rows=[], row_count=0, timing_ms=0.5),
            answer_generation=AnswerGenerationOutput(answer="ok", timing_ms=1.0, llm_stats={}, error=None),
        )

    def test_miss_returns_none(self):
        from src.pipeline import QueryCache

        cache = QueryCache(max_size=4)
        self.assertIsNone(cache.get("unknown question"))
        self.assertEqual(cache.misses, 1)

    def test_hit_returns_stored_result(self):
        from src.pipeline import QueryCache

        cache = QueryCache(max_size=4)
        out = self._make_output("q1")
        cache.put("q1", out)
        hit = cache.get("q1")
        self.assertIsNotNone(hit)
        self.assertEqual(hit.question, "q1")
        self.assertEqual(cache.hits, 1)

    def test_eviction_at_max_size(self):
        from src.pipeline import QueryCache

        cache = QueryCache(max_size=2)
        cache.put("a", self._make_output("a"))
        cache.put("b", self._make_output("b"))
        cache.put("c", self._make_output("c"))
        self.assertIsNone(cache.get("a"))
        self.assertIsNotNone(cache.get("b"))
        self.assertIsNotNone(cache.get("c"))

    def test_lru_order_preserved(self):
        from src.pipeline import QueryCache

        cache = QueryCache(max_size=2)
        cache.put("a", self._make_output("a"))
        cache.put("b", self._make_output("b"))
        cache.get("a")  # touch "a" so "b" becomes least-recently-used
        cache.put("c", self._make_output("c"))
        self.assertIsNotNone(cache.get("a"))
        self.assertIsNone(cache.get("b"))


class TestExplainValidation(unittest.TestCase):
    """EXPLAIN-based schema validation against a real temp database."""

    @classmethod
    def setUpClass(cls):
        cls._tmpfile = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
        cls._db_path = Path(cls._tmpfile.name)
        cls._tmpfile.close()
        conn = sqlite3.connect(cls._db_path)
        conn.execute("CREATE TABLE gaming_mental_health (age INTEGER, gender TEXT, score REAL)")
        conn.execute("INSERT INTO gaming_mental_health VALUES (25, 'Male', 5.0)")
        conn.commit()
        conn.close()

    @classmethod
    def tearDownClass(cls):
        cls._db_path.unlink(missing_ok=True)

    def _explain(self, sql: str) -> str | None:
        try:
            with sqlite3.connect(self._db_path, timeout=5) as conn:
                conn.execute(f"EXPLAIN {sql}")
            return None
        except sqlite3.OperationalError as e:
            return str(e)

    def test_valid_query_passes(self):
        self.assertIsNone(self._explain("SELECT age, gender FROM gaming_mental_health"))

    def test_invalid_column_fails(self):
        err = self._explain("SELECT nonexistent_col FROM gaming_mental_health")
        self.assertIsNotNone(err)
        self.assertIn("no such column", err.lower())

    def test_invalid_table_fails(self):
        err = self._explain("SELECT * FROM nonexistent_table")
        self.assertIsNotNone(err)
        self.assertIn("no such table", err.lower())

    def test_aggregation_passes(self):
        self.assertIsNone(self._explain("SELECT gender, AVG(score) FROM gaming_mental_health GROUP BY gender"))


class TestModelFallbackInit(unittest.TestCase):
    """Verify fallback model configuration."""

    def test_default_fallback_models_populated(self):
        client = OpenRouterLLMClient.__new__(OpenRouterLLMClient)
        client.model = "openai/gpt-4.1-nano"
        client._fallback_models = ["google/gemini-2.0-flash-lite:free"]
        client._last_model_used = client.model
        self.assertEqual(len(client._fallback_models), 1)
        self.assertEqual(client._last_model_used, "openai/gpt-4.1-nano")

    def test_custom_fallback_models(self):
        client = OpenRouterLLMClient.__new__(OpenRouterLLMClient)
        client._fallback_models = ["model-a", "model-b"]
        self.assertEqual(client._fallback_models, ["model-a", "model-b"])


class TestTokenStats(unittest.TestCase):
    """Test pop_stats returns correct structure."""

    def test_pop_stats_returns_zeros_initially(self):
        client = OpenRouterLLMClient.__new__(OpenRouterLLMClient)
        client._stats = {
            "llm_calls": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }
        stats = client.pop_stats()
        self.assertEqual(stats["llm_calls"], 0)
        self.assertEqual(stats["prompt_tokens"], 0)
        self.assertEqual(stats["completion_tokens"], 0)
        self.assertEqual(stats["total_tokens"], 0)

    def test_pop_stats_resets_after_pop(self):
        client = OpenRouterLLMClient.__new__(OpenRouterLLMClient)
        client._stats = {
            "llm_calls": 3,
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "total_tokens": 150,
        }
        first = client.pop_stats()
        second = client.pop_stats()
        self.assertEqual(first["llm_calls"], 3)
        self.assertEqual(first["total_tokens"], 150)
        self.assertEqual(second["llm_calls"], 0)
        self.assertEqual(second["total_tokens"], 0)


if __name__ == "__main__":
    unittest.main()
